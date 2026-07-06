//! C ABI bridge for the Anki Rust backend (rslib), targeting iOS via XCFramework.
//!
//! Architecture (adapted from AMGI, github.com/antigluten/amgi):
//!   * A single `Backend` instance is created via `anki_open_backend`, returning an
//!     opaque pointer (as `int64_t`).
//!   * All RPCs route through `Backend::run_service_method(service, method, &[u8])`,
//!     which decodes a protobuf request and returns a protobuf response (or a
//!     protobuf-encoded `BackendError` on failure). This is the same generic
//!     dispatch AnkiDroid's JNI bridge uses.
//!   * Memory ownership: any `out_data` buffer returned to Swift is heap-allocated
//!     by Rust and MUST be released with `anki_free_response`. Swift never frees
//!     Rust memory directly, and never touches the SQLite DB (single-writer, owned
//!     by Rust).
//!
//! On top of the generic dispatch we expose four *named* convenience functions
//! required by the G-iOS track — `anki_open`, `anki_get_queued_cards`,
//! `anki_answer_card`, `anki_close` — which hardcode the protobuf service/method
//! indices verified against rslib's generated `backend.rs`:
//!
//!   OpenCollection   -> BackendCollectionService (svc 3, method 0)
//!   CloseCollection  -> BackendCollectionService (svc 3, method 1)
//!   GetQueuedCards   -> BackendSchedulerService  (svc 13, method 3)
//!   AnswerCard       -> BackendSchedulerService  (svc 13, method 4)

use std::collections::HashMap;
use std::ffi::CStr;
use std::os::raw::c_char;
use std::os::raw::c_int;
use std::slice;
use std::sync::Mutex;
use std::sync::OnceLock;

use anki::backend::init_backend;
use anki::backend::Backend;
use prost::Message;

// --- Verified protobuf service/method indices (from rslib generated backend.rs) ---
//
// rslib exposes two parallel dispatch tables; `Backend::run_service_method`
// (backend.rs ~line 6662) routes to the *backend* services at ODD indices.
const SVC_BACKEND_COLLECTION: u32 = 3;
const M_OPEN_COLLECTION: u32 = 0;
const M_CLOSE_COLLECTION: u32 = 1;

const SVC_BACKEND_SCHEDULER: u32 = 13;
const M_GET_QUEUED_CARDS: u32 = 3;
const M_ANSWER_CARD: u32 = 4;
// SchedulerService method indices mirror declaration order in scheduler.proto.
// GetTopicMasteryStats is 39; GetTopicBreakdown (T4) was inserted next to it, so
// it takes 40 and pushes GetGmatScores/GradeAnswer down by one. Verified against
// generated _backend_generated.py (get_topic_breakdown -> _run_command(13, 40),
// get_gmat_scores -> 41, grade_answer -> 42).
const M_GET_TOPIC_BREAKDOWN: u32 = 40;
const M_GET_GMAT_SCORES: u32 = 41;
const M_GRADE_ANSWER: u32 = 42;

// BackendCardRenderingService (svc 27): RenderExistingCard is method 6.
const SVC_BACKEND_CARD_RENDERING: u32 = 27;
const M_RENDER_EXISTING_CARD: u32 = 6;

// BackendImportExportService (svc 39): ImportAnkiPackage is method 2.
const SVC_BACKEND_IMPORT_EXPORT: u32 = 39;
const M_IMPORT_ANKI_PACKAGE: u32 = 2;

// BackendDecksService (svc 7): GetDeckIdByName=method 7, SetCurrentDeck=method 22.
const SVC_BACKEND_DECKS: u32 = 7;
const M_GET_DECK_ID_BY_NAME: u32 = 7;
const M_SET_CURRENT_DECK: u32 = 22;

// BackendSyncService (svc 1): SyncLogin=method 3, SyncCollection=method 5,
// FullUploadOrDownload=method 6.
const SVC_BACKEND_SYNC: u32 = 1;
const M_SYNC_LOGIN: u32 = 3;
const M_SYNC_COLLECTION: u32 = 5;
const M_FULL_UPLOAD_OR_DOWNLOAD: u32 = 6;

/// Process-global stash of the SchedulingStates rslib most recently returned for
/// each card via `anki_next_card`. AnswerCard requires the card's `current` and
/// rating-selected `new` SchedulingState; rather than make Swift parse protobuf,
/// we keep the rslib-produced states here and rebuild the CardAnswer in Rust.
fn states_store() -> &'static Mutex<HashMap<i64, anki_proto::scheduler::SchedulingStates>> {
    static STORE: OnceLock<Mutex<HashMap<i64, anki_proto::scheduler::SchedulingStates>>> =
        OnceLock::new();
    STORE.get_or_init(|| Mutex::new(HashMap::new()))
}

// ====================================================================
// Core lifecycle / generic dispatch (AMGI-compatible)
// ====================================================================

/// Create a new Anki backend instance.
///
/// # Safety
/// - `init_data` must point to a valid buffer of `init_len` bytes containing a
///   serialized `BackendInit` protobuf message, or be null for defaults.
/// - `out_ptr` must point to writable memory for a single `int64_t`.
///
/// Returns 0 on success, -1 on error.
#[no_mangle]
pub unsafe extern "C" fn anki_open_backend(
    init_data: *const u8,
    init_len: usize,
    out_ptr: *mut i64,
) -> c_int {
    let init_bytes: &[u8] = if init_data.is_null() || init_len == 0 {
        b""
    } else {
        unsafe { slice::from_raw_parts(init_data, init_len) }
    };

    // If empty, encode a default BackendInit (empty preferred_langs, server=false).
    let effective_bytes: Vec<u8>;
    let bytes_to_use = if init_bytes.is_empty() {
        let default_init = anki_proto::backend::BackendInit::default();
        effective_bytes = default_init.encode_to_vec();
        &effective_bytes
    } else {
        init_bytes
    };

    match init_backend(bytes_to_use) {
        Ok(backend) => {
            let boxed = Box::new(backend);
            let ptr = Box::into_raw(boxed) as i64;
            unsafe { *out_ptr = ptr };
            0
        }
        Err(_e) => -1,
    }
}

/// Execute a backend RPC method via protobuf.
///
/// # Safety
/// - `backend_ptr` must be a valid pointer returned by `anki_open_backend`.
/// - `input_data`/`input_len` must describe a valid protobuf request (or be null/0).
/// - `out_data`/`out_len` receive the response. Caller MUST free with
///   `anki_free_response`.
///
/// Returns:
///   0  on success (out_data has the response protobuf),
///   1  on backend error (out_data has a serialized BackendError protobuf),
///  -1  on FFI error (null backend pointer).
#[no_mangle]
pub unsafe extern "C" fn anki_run_method(
    backend_ptr: i64,
    service: u32,
    method: u32,
    input_data: *const u8,
    input_len: usize,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let backend = unsafe { &*(backend_ptr as *const Backend) };

    let input = if input_data.is_null() || input_len == 0 {
        &[][..]
    } else {
        unsafe { slice::from_raw_parts(input_data, input_len) }
    };

    match backend.run_service_method(service, method, input) {
        Ok(output) => {
            unsafe { set_output(output, out_data, out_len) };
            0
        }
        Err(err_bytes) => {
            unsafe { set_output(err_bytes, out_data, out_len) };
            1
        }
    }
}

/// Free a response buffer allocated by `anki_run_method` / the named wrappers.
///
/// # Safety
/// `data`/`len` must be a buffer previously produced by this library, or null/0.
#[no_mangle]
pub unsafe extern "C" fn anki_free_response(data: *mut u8, len: usize) {
    if !data.is_null() && len > 0 {
        let _ = unsafe { Vec::from_raw_parts(data, len, len) };
    }
}

/// Close and destroy the backend instance.
///
/// # Safety
/// `backend_ptr` must be a pointer from `anki_open_backend`; invalid afterwards.
#[no_mangle]
pub unsafe extern "C" fn anki_close_backend(backend_ptr: i64) {
    if backend_ptr != 0 {
        let _ = unsafe { Box::from_raw(backend_ptr as *mut Backend) };
    }
}

// ====================================================================
// Named convenience wrappers (G-iOS track requirement)
// ====================================================================

/// Open (or create) a collection at `collection_path`.
///
/// This is the key proof-of-life call: it routes OpenCollection through the real
/// rslib backend, which opens/creates the SQLite collection on disk.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend`.
/// - `collection_path` must be a valid NUL-terminated UTF-8 C string.
/// - `media_folder_path` / `media_db_path` may be null (empty) or NUL-terminated
///   UTF-8 C strings.
///
/// Returns 0 on success, 1 on backend error, -1 on FFI error (bad pointer/string).
#[no_mangle]
pub unsafe extern "C" fn anki_open(
    backend_ptr: i64,
    collection_path: *const c_char,
    media_folder_path: *const c_char,
    media_db_path: *const c_char,
) -> c_int {
    if backend_ptr == 0 || collection_path.is_null() {
        return -1;
    }

    let Some(collection_path) = (unsafe { cstr_to_string(collection_path) }) else {
        return -1;
    };
    let media_folder_path = unsafe { cstr_to_string(media_folder_path) }.unwrap_or_default();
    let media_db_path = unsafe { cstr_to_string(media_db_path) }.unwrap_or_default();

    let req = anki_proto::collection::OpenCollectionRequest {
        collection_path,
        media_folder_path,
        media_db_path,
    };
    let input = req.encode_to_vec();

    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_COLLECTION, M_OPEN_COLLECTION, &input) {
        Ok(_) => 0,
        Err(_err_bytes) => 1,
    }
}

/// Fetch the currently queued cards as a serialized `QueuedCards` protobuf.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection already open.
/// - `out_data`/`out_len` receive the protobuf response; free with
///   `anki_free_response`.
///
/// Returns 0 on success, 1 on backend error (out has BackendError), -1 on FFI error.
#[no_mangle]
pub unsafe extern "C" fn anki_get_queued_cards(
    backend_ptr: i64,
    fetch_limit: u32,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let req = anki_proto::scheduler::GetQueuedCardsRequest {
        fetch_limit,
        intraday_learning_only: false,
    };
    let input = req.encode_to_vec();

    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_GET_QUEUED_CARDS, &input) {
        Ok(output) => {
            unsafe { set_output(output, out_data, out_len) };
            0
        }
        Err(err_bytes) => {
            unsafe { set_output(err_bytes, out_data, out_len) };
            1
        }
    }
}

/// Answer a card. `card_answer_data` is a serialized `CardAnswer` protobuf.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
/// - `card_answer_data`/`card_answer_len` must describe a valid `CardAnswer` proto.
/// - `out_data`/`out_len` receive the `OpChanges` protobuf; free with
///   `anki_free_response`.
///
/// Returns 0 on success, 1 on backend error, -1 on FFI error.
#[no_mangle]
pub unsafe extern "C" fn anki_answer_card(
    backend_ptr: i64,
    card_answer_data: *const u8,
    card_answer_len: usize,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let input = if card_answer_data.is_null() || card_answer_len == 0 {
        &[][..]
    } else {
        unsafe { slice::from_raw_parts(card_answer_data, card_answer_len) }
    };

    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_ANSWER_CARD, input) {
        Ok(output) => {
            unsafe { set_output(output, out_data, out_len) };
            0
        }
        Err(err_bytes) => {
            unsafe { set_output(err_bytes, out_data, out_len) };
            1
        }
    }
}

/// Close the currently open collection.
///
/// # Safety
/// `backend_ptr` must be from `anki_open_backend`.
///
/// Returns 0 on success, 1 on backend error, -1 on FFI error.
#[no_mangle]
pub unsafe extern "C" fn anki_close(backend_ptr: i64, downgrade_to_schema11: bool) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let req = anki_proto::collection::CloseCollectionRequest {
        downgrade_to_schema11,
    };
    let input = req.encode_to_vec();

    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_COLLECTION, M_CLOSE_COLLECTION, &input) {
        Ok(_) => 0,
        Err(_err_bytes) => 1,
    }
}

// ====================================================================
// High-level review-loop helpers (T5 iOS review session)
//
// These do all protobuf marshalling in Rust so the Swift side never has to
// parse or build protobuf messages. The card-rendering and import RPCs route
// through the SAME generic `Backend::run_service_method` dispatch the rest of
// the bridge uses.
// ====================================================================

/// Import an .apkg package into the currently open collection via rslib's
/// ImportAnkiPackage RPC (BackendImportExportService svc 39 / method 2).
///
/// Uses default options EXCEPT `with_scheduling = false`, so every imported
/// card enters as a fresh "new" card that is immediately due for review (we
/// want a study session today, not whatever schedule the deck was exported
/// with). `with_deck_configs = true` keeps the deck's own config.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
/// - `package_path` must be a valid NUL-terminated UTF-8 C string.
///
/// Returns 0 on success, 1 on backend error, -1 on FFI error (bad ptr/string).
#[no_mangle]
pub unsafe extern "C" fn anki_import_apkg(
    backend_ptr: i64,
    package_path: *const c_char,
) -> c_int {
    if backend_ptr == 0 || package_path.is_null() {
        return -1;
    }
    let Some(package_path) = (unsafe { cstr_to_string(package_path) }) else {
        return -1;
    };
    if package_path.is_empty() {
        return -1;
    }

    let req = anki_proto::import_export::ImportAnkiPackageRequest {
        package_path,
        options: Some(anki_proto::import_export::ImportAnkiPackageOptions {
            merge_notetypes: false,
            update_notes:
                anki_proto::import_export::ImportAnkiPackageUpdateCondition::Always as i32,
            update_notetypes:
                anki_proto::import_export::ImportAnkiPackageUpdateCondition::Always as i32,
            // Import scheduling + revlog so the deck's baked demo history (the
            // mid-progress state that drives the three scores + topic coverage)
            // comes in with the cards, not just blank new cards.
            with_scheduling: true,
            with_deck_configs: true,
        }),
    };
    let input = req.encode_to_vec();

    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_IMPORT_EXPORT, M_IMPORT_ANKI_PACKAGE, &input) {
        Ok(_) => 0,
        Err(_err_bytes) => 1,
    }
}

/// Select the deck with the given name as the current deck, so its cards (and
/// its children's cards) populate the scheduler queue.
///
/// GetQueuedCards builds the study queue from the *currently selected* deck. A
/// freshly opened collection selects the Default deck, so after importing the
/// GMAT deck we must point the scheduler at it. Resolves the name to a DeckId
/// via GetDeckIdByName, then SetCurrentDeck (both BackendDecksService, svc 7).
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
/// - `deck_name` must be a valid NUL-terminated UTF-8 C string.
///
/// Returns 0 on success, 1 on backend error / unknown deck, -1 on FFI error.
#[no_mangle]
pub unsafe extern "C" fn anki_select_deck_by_name(
    backend_ptr: i64,
    deck_name: *const c_char,
) -> c_int {
    if backend_ptr == 0 || deck_name.is_null() {
        return -1;
    }
    let Some(deck_name) = (unsafe { cstr_to_string(deck_name) }) else {
        return -1;
    };
    let backend = unsafe { &*(backend_ptr as *const Backend) };

    // 1. Resolve the deck name to a DeckId.
    let name_req = anki_proto::generic::String { val: deck_name };
    let name_bytes = name_req.encode_to_vec();
    let id_bytes =
        match backend.run_service_method(SVC_BACKEND_DECKS, M_GET_DECK_ID_BY_NAME, &name_bytes) {
            Ok(b) => b,
            Err(_) => return 1,
        };
    let deck_id = match anki_proto::decks::DeckId::decode(&id_bytes[..]) {
        Ok(d) => d,
        Err(_) => return 1,
    };
    // did == 0 means the deck was not found.
    if deck_id.did == 0 {
        return 1;
    }

    // 2. Set it as the current deck.
    let id_req = anki_proto::decks::DeckId { did: deck_id.did };
    let id_req_bytes = id_req.encode_to_vec();
    match backend.run_service_method(SVC_BACKEND_DECKS, M_SET_CURRENT_DECK, &id_req_bytes) {
        Ok(_) => 0,
        Err(_) => 1,
    }
}

/// Select the deck with the given `deck_id` as the current deck, so its cards
/// (and its children's) populate the scheduler queue.
///
/// This is the by-id companion to `anki_select_deck_by_name`: when a caller
/// already holds a resolved DeckId (e.g. a per-topic subdeck id it looked up
/// once, or the parent 'GMAT Focus' exam deck id), it can point the scheduler
/// straight at it without a name round-trip. Routes SetCurrentDeck through
/// BackendDecksService (svc 7, method 22), the same RPC the name path ends in.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
///
/// Returns 0 on success, 1 on backend error, -1 on FFI error (bad pointer /
/// non-positive id).
#[no_mangle]
pub unsafe extern "C" fn anki_select_deck(backend_ptr: i64, deck_id: i64) -> c_int {
    if backend_ptr == 0 || deck_id <= 0 {
        return -1;
    }
    let backend = unsafe { &*(backend_ptr as *const Backend) };
    let id_req = anki_proto::decks::DeckId { did: deck_id };
    let id_req_bytes = id_req.encode_to_vec();
    match backend.run_service_method(SVC_BACKEND_DECKS, M_SET_CURRENT_DECK, &id_req_bytes) {
        Ok(_) => 0,
        Err(_) => 1,
    }
}

/// Fetch the per-topic × per-difficulty-band breakdown (T4) and return it as a
/// JSON blob (caller frees with `anki_free_response`). Scoped to the whole
/// collection like `anki_get_scores`.
///
/// `topic_depth` groups by tag-prefix depth (0 -> default 2 = `Section::Topic`).
///
/// Output JSON (UTF-8): mirrors `anki_get_scores` in that the RPC's protobuf
/// response is decoded in Rust and hand-serialized to JSON so the Swift side
/// (which has no SwiftProtobuf) can decode it with JSONSerialization:
///   {"topics":[{"topic":"<Section::Topic::Subtopic>","reviewed_cards":<int>,
///               "easy":{"total":<int>,"attempted":<int>,"correct":<int>,"accuracy":<float>},
///               "medium":{...},"hard":{...}}, ...]}
/// One object per TopicDifficultyBreakdown; each band has keys total/attempted/
/// correct/accuracy. A missing band submessage serializes as all-zero counts.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
/// - `out_data`/`out_len` receive the JSON bytes; free with `anki_free_response`.
///
/// Returns 0 on success, 1 on backend error (out has BackendError), -1 on FFI
/// error.
#[no_mangle]
pub unsafe extern "C" fn anki_get_topic_breakdown(
    backend_ptr: i64,
    topic_depth: u32,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let backend = unsafe { &*(backend_ptr as *const Backend) };

    let req = anki_proto::scheduler::GetTopicBreakdownRequest { topic_depth };
    let req_bytes = req.encode_to_vec();
    let resp_bytes =
        match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_GET_TOPIC_BREAKDOWN, &req_bytes) {
            Ok(b) => b,
            Err(_) => return 1,
        };
    let breakdown =
        match anki_proto::scheduler::GetTopicBreakdownResponse::decode(&resp_bytes[..]) {
            Ok(b) => b,
            Err(_) => return 1,
        };

    // Hand-build the JSON (no serde dependency), mirroring `anki_get_scores`.
    fn push_band(band: &Option<anki_proto::scheduler::DifficultyBand>, out: &mut String) {
        let band = band.clone().unwrap_or_default();
        out.push_str("{\"total\":");
        out.push_str(&band.total.to_string());
        out.push_str(",\"attempted\":");
        out.push_str(&band.attempted.to_string());
        out.push_str(",\"correct\":");
        out.push_str(&band.correct.to_string());
        out.push_str(",\"accuracy\":");
        out.push_str(&band.accuracy.to_string());
        out.push('}');
    }

    let mut json = String::with_capacity(512);
    json.push_str("{\"topics\":[");
    for (i, topic) in breakdown.topics.iter().enumerate() {
        if i > 0 {
            json.push(',');
        }
        json.push_str("{\"topic\":\"");
        json_escape_into(&topic.topic, &mut json);
        json.push_str("\",\"reviewed_cards\":");
        json.push_str(&topic.reviewed_cards.to_string());
        json.push_str(",\"easy\":");
        push_band(&topic.easy, &mut json);
        json.push_str(",\"medium\":");
        push_band(&topic.medium, &mut json);
        json.push_str(",\"hard\":");
        push_band(&topic.hard, &mut json);
        json.push('}');
    }
    json.push_str("]}");

    unsafe { set_output(json.into_bytes(), out_data, out_len) };
    0
}

/// Fetch the next queued card, render it through rslib, and return a small JSON
/// blob describing it. The card's SchedulingStates are stashed in `states_store`
/// keyed by card_id so a later `anki_answer_rating` can rebuild the CardAnswer.
///
/// Output JSON (UTF-8, caller frees with `anki_free_response`):
///   {"card_id":123,"question":"<html>","answer":"<html>","css":"..."}
/// When the queue is empty, returns: {"empty":true}
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
/// - `out_data`/`out_len` receive the JSON bytes; free with `anki_free_response`.
///
/// Returns 0 on success (incl. empty queue), 1 on backend error, -1 on FFI error.
#[no_mangle]
pub unsafe extern "C" fn anki_next_card(
    backend_ptr: i64,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let backend = unsafe { &*(backend_ptr as *const Backend) };

    // 1. Ask rslib for the queued cards.
    let q_req = anki_proto::scheduler::GetQueuedCardsRequest {
        fetch_limit: 1,
        intraday_learning_only: false,
    };
    let q_bytes = q_req.encode_to_vec();
    let queued_bytes =
        match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_GET_QUEUED_CARDS, &q_bytes) {
            Ok(b) => b,
            Err(_) => return 1,
        };
    let queued = match anki_proto::scheduler::QueuedCards::decode(&queued_bytes[..]) {
        Ok(q) => q,
        Err(_) => return 1,
    };

    let Some(first) = queued.cards.into_iter().next() else {
        // Empty queue / congrats screen.
        let json = b"{\"empty\":true}".to_vec();
        unsafe { set_output(json, out_data, out_len) };
        return 0;
    };

    let card = match first.card {
        Some(c) => c,
        None => return 1,
    };
    let card_id = card.id;

    // Stash the rslib-produced scheduling states for the answer step.
    if let Some(states) = first.states {
        states_store().lock().unwrap().insert(card_id, states);
    }

    // 2. Render the card through rslib (partial_render=false => fully filtered).
    let r_req = anki_proto::card_rendering::RenderExistingCardRequest {
        card_id,
        browser: false,
        partial_render: false,
    };
    let r_bytes = r_req.encode_to_vec();
    let render_bytes = match backend.run_service_method(
        SVC_BACKEND_CARD_RENDERING,
        M_RENDER_EXISTING_CARD,
        &r_bytes,
    ) {
        Ok(b) => b,
        Err(_) => return 1,
    };
    let rendered = match anki_proto::card_rendering::RenderCardResponse::decode(&render_bytes[..]) {
        Ok(r) => r,
        Err(_) => return 1,
    };

    let question = nodes_to_html(&rendered.question_nodes);
    let answer = nodes_to_html(&rendered.answer_nodes);

    // 3. Hand-build a small JSON blob (avoids a serde_json dependency).
    let mut json = String::with_capacity(question.len() + answer.len() + rendered.css.len() + 64);
    json.push_str("{\"card_id\":");
    json.push_str(&card_id.to_string());
    json.push_str(",\"question\":\"");
    json_escape_into(&question, &mut json);
    json.push_str("\",\"answer\":\"");
    json_escape_into(&answer, &mut json);
    json.push_str("\",\"css\":\"");
    json_escape_into(&rendered.css, &mut json);
    json.push_str("\"}");

    unsafe { set_output(json.into_bytes(), out_data, out_len) };
    0
}

/// Compute the three GMAT scores (memory / performance / readiness) and return
/// them as a JSON blob (caller frees with `anki_free_response`). Scoped to the
/// whole collection (the phone/desktop use a single GMAT deck).
///
/// Output JSON: {"memory":SV,"performance":SV,"readiness":SV} where each SV is
/// {"abstained":bool,"score":f,"low":f,"high":f,"unit":"pct|gmat",
///  "confidence":"","reasons":[..],"missing":[..]}.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
/// - `out_data`/`out_len` receive the JSON bytes; free with `anki_free_response`.
///
/// Returns 0 on success, 1 on backend error, -1 on FFI error.
#[no_mangle]
pub unsafe extern "C" fn anki_get_scores(
    backend_ptr: i64,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let backend = unsafe { &*(backend_ptr as *const Backend) };

    let req = anki_proto::scheduler::GetGmatScoresRequest {
        deck_name: String::new(),
    };
    let req_bytes = req.encode_to_vec();
    let resp_bytes =
        match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_GET_GMAT_SCORES, &req_bytes) {
            Ok(b) => b,
            Err(_) => return 1,
        };
    let scores = match anki_proto::scheduler::GmatScores::decode(&resp_bytes[..]) {
        Ok(s) => s,
        Err(_) => return 1,
    };

    // Hand-build the JSON (no serde dependency), mirroring `anki_next_card`.
    fn push_score(sv: &Option<anki_proto::scheduler::ScoreValue>, out: &mut String) {
        let sv = sv.clone().unwrap_or_default();
        out.push_str("{\"abstained\":");
        out.push_str(if sv.abstained { "true" } else { "false" });
        out.push_str(",\"score\":");
        out.push_str(&sv.score.to_string());
        out.push_str(",\"low\":");
        out.push_str(&sv.low.to_string());
        out.push_str(",\"high\":");
        out.push_str(&sv.high.to_string());
        out.push_str(",\"unit\":\"");
        json_escape_into(&sv.unit, out);
        out.push_str("\",\"confidence\":\"");
        json_escape_into(&sv.confidence, out);
        out.push_str("\",\"reasons\":[");
        for (i, r) in sv.reasons.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            out.push('"');
            json_escape_into(r, out);
            out.push('"');
        }
        out.push_str("],\"missing\":[");
        for (i, m) in sv.missing.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            out.push('"');
            json_escape_into(m, out);
            out.push('"');
        }
        out.push_str("]}");
    }

    let mut json = String::with_capacity(512);
    json.push_str("{\"memory\":");
    push_score(&scores.memory, &mut json);
    json.push_str(",\"performance\":");
    push_score(&scores.performance, &mut json);
    json.push_str(",\"readiness\":");
    push_score(&scores.readiness, &mut json);
    json.push('}');

    unsafe { set_output(json.into_bytes(), out_data, out_len) };
    0
}

/// Auto-grade a tapped multiple-choice answer into an Anki ease (1..=4) using the
/// shared engine: the rating comes from correctness × the student's confidence
/// (not from time). Pair the result with `anki_answer_rating` to record the
/// review. The caller can derive the "overconfident miss" flag itself (wrong +
/// confidence > 0); the desktop path reads it from the RPC response.
///
/// `correct`: 0 = wrong, non-zero = right. `confidence`: 0 = guessing,
/// 1 = fairly sure, 2 = confident.
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
///
/// Returns the ease 1..=4 on success; -1 on FFI error, -2 on backend error,
/// -3 on decode error.
#[no_mangle]
pub unsafe extern "C" fn anki_grade_answer(
    backend_ptr: i64,
    correct: u8,
    confidence: u32,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let backend = unsafe { &*(backend_ptr as *const Backend) };
    let req = anki_proto::scheduler::GradeAnswerRequest {
        correct: correct != 0,
        confidence,
    };
    let req_bytes = req.encode_to_vec();
    let resp_bytes =
        match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_GRADE_ANSWER, &req_bytes) {
            Ok(b) => b,
            Err(_) => return -2,
        };
    match anki_proto::scheduler::GradeAnswerResponse::decode(&resp_bytes[..]) {
        Ok(r) => r.ease as c_int,
        Err(_) => -3,
    }
}

/// Answer the given card with a rating, rebuilding the CardAnswer from the
/// SchedulingStates rslib returned for that card in the prior `anki_next_card`.
///
/// `rating`: 1=Again, 2=Hard, 3=Good, 4=Easy (the UI's natural 1..=4 order;
/// mapped to the proto enum AGAIN=0/HARD=1/GOOD=2/EASY=3 internally).
///
/// # Safety
/// - `backend_ptr` must be from `anki_open_backend` with a collection open.
///
/// Returns 0 on success, 1 on backend error, -1 on FFI error / unknown card /
/// invalid rating.
#[no_mangle]
pub unsafe extern "C" fn anki_answer_rating(
    backend_ptr: i64,
    card_id: i64,
    rating: u32,
) -> c_int {
    if backend_ptr == 0 || !(1..=4).contains(&rating) {
        return -1;
    }

    let states = match states_store().lock().unwrap().get(&card_id).cloned() {
        Some(s) => s,
        None => return -1, // no states stashed for this card
    };

    // current_state comes from `current`; new_state is the rating-selected one.
    let current_state = states.current;
    let (new_state, proto_rating) = match rating {
        1 => (states.again, anki_proto::scheduler::card_answer::Rating::Again),
        2 => (states.hard, anki_proto::scheduler::card_answer::Rating::Hard),
        3 => (states.good, anki_proto::scheduler::card_answer::Rating::Good),
        4 => (states.easy, anki_proto::scheduler::card_answer::Rating::Easy),
        _ => return -1,
    };
    let (Some(current_state), Some(new_state)) = (current_state, new_state) else {
        return -1;
    };

    let now_millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);

    let answer = anki_proto::scheduler::CardAnswer {
        card_id,
        current_state: Some(current_state),
        new_state: Some(new_state),
        rating: proto_rating as i32,
        answered_at_millis: now_millis,
        milliseconds_taken: 0,
    };
    let input = answer.encode_to_vec();

    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_ANSWER_CARD, &input) {
        Ok(_) => {
            states_store().lock().unwrap().remove(&card_id);
            0
        }
        Err(_) => 1,
    }
}

/// Log in to a sync server. Returns a serialized SyncAuth (caller frees via anki_free_response).
/// # Safety: standard FFI pointer contract; strings are NUL-terminated UTF-8.
#[no_mangle]
pub unsafe extern "C" fn anki_sync_login(
    backend_ptr: i64,
    endpoint: *const c_char,
    user: *const c_char,
    pass: *const c_char,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 || user.is_null() || pass.is_null() { return -1; }
    let Some(username) = (unsafe { cstr_to_string(user) }) else { return -1; };
    let Some(password) = (unsafe { cstr_to_string(pass) }) else { return -1; };
    let endpoint = unsafe { cstr_to_string(endpoint) }.unwrap_or_default();
    let req = anki_proto::sync::SyncLoginRequest {
        username,
        password,
        endpoint: if endpoint.is_empty() { None } else { Some(endpoint) },
    };
    let input = req.encode_to_vec();
    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_SYNC, M_SYNC_LOGIN, &input) {
        Ok(output) => { unsafe { set_output(output, out_data, out_len) }; 0 }
        Err(err_bytes) => { unsafe { set_output(err_bytes, out_data, out_len) }; 1 }
    }
}

/// Run a collection sync using a serialized SyncAuth from anki_sync_login.
/// Returns a serialized SyncCollectionResponse (inspect `.required` for full-sync).
/// # Safety: auth_data/auth_len describe a valid SyncAuth protobuf.
#[no_mangle]
pub unsafe extern "C" fn anki_sync_collection(
    backend_ptr: i64,
    auth_data: *const u8,
    auth_len: usize,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 || auth_data.is_null() || auth_len == 0 { return -1; }
    let auth_bytes = unsafe { slice::from_raw_parts(auth_data, auth_len) };
    let auth = match anki_proto::sync::SyncAuth::decode(auth_bytes) { Ok(a) => a, Err(_) => return -1 };
    let req = anki_proto::sync::SyncCollectionRequest { auth: Some(auth), sync_media: false };
    let input = req.encode_to_vec();
    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_SYNC, M_SYNC_COLLECTION, &input) {
        Ok(output) => { unsafe { set_output(output, out_data, out_len) }; 0 }
        Err(err_bytes) => { unsafe { set_output(err_bytes, out_data, out_len) }; 1 }
    }
}

/// Full upload or download (used when SyncCollectionResponse.required is a full-sync variant).
/// upload=true -> full_upload; false -> full_download. server_usn < 0 omits media sync.
/// # Safety: auth_data/auth_len describe a valid SyncAuth protobuf.
#[no_mangle]
pub unsafe extern "C" fn anki_full_upload_or_download(
    backend_ptr: i64,
    auth_data: *const u8,
    auth_len: usize,
    upload: bool,
    server_usn: i32,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 || auth_data.is_null() || auth_len == 0 { return -1; }
    let auth_bytes = unsafe { slice::from_raw_parts(auth_data, auth_len) };
    let auth = match anki_proto::sync::SyncAuth::decode(auth_bytes) { Ok(a) => a, Err(_) => return -1 };
    let req = anki_proto::sync::FullUploadOrDownloadRequest {
        auth: Some(auth),
        upload,
        server_usn: if server_usn < 0 { None } else { Some(server_usn) },
    };
    let input = req.encode_to_vec();
    let backend = unsafe { &*(backend_ptr as *const Backend) };
    match backend.run_service_method(SVC_BACKEND_SYNC, M_FULL_UPLOAD_OR_DOWNLOAD, &input) {
        Ok(output) => { unsafe { set_output(output, out_data, out_len) }; 0 }
        Err(err_bytes) => { unsafe { set_output(err_bytes, out_data, out_len) }; 1 }
    }
}

// ====================================================================
// Helpers
// ====================================================================

/// Concatenate rendered template nodes into final HTML, matching how Anki's
/// own frontends assemble a card: Text nodes contribute their text, fully
/// rendered Replacement nodes contribute their `current_text`.
fn nodes_to_html(nodes: &[anki_proto::card_rendering::RenderedTemplateNode]) -> String {
    use anki_proto::card_rendering::rendered_template_node::Value;
    let mut out = String::new();
    for node in nodes {
        match &node.value {
            Some(Value::Text(t)) => out.push_str(t),
            Some(Value::Replacement(r)) => out.push_str(&r.current_text),
            None => {}
        }
    }
    out
}

/// Append `s` to `out` with JSON string-escaping (quotes, backslashes, control
/// chars). Card HTML is otherwise emitted verbatim.
fn json_escape_into(s: &str, out: &mut String) {
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
}

/// Convert response bytes into a heap buffer owned by the caller (freed via
/// `anki_free_response`).
unsafe fn set_output(data: Vec<u8>, out_data: *mut *mut u8, out_len: *mut usize) {
    let len = data.len();
    if len > 0 {
        let mut boxed = data.into_boxed_slice();
        let ptr = boxed.as_mut_ptr();
        std::mem::forget(boxed);
        unsafe {
            *out_data = ptr;
            *out_len = len;
        }
    } else {
        unsafe {
            *out_data = std::ptr::null_mut();
            *out_len = 0;
        }
    }
}

/// Convert a (possibly null) NUL-terminated C string into an owned String.
/// Returns None for invalid UTF-8; null pointers yield an empty string.
unsafe fn cstr_to_string(ptr: *const c_char) -> Option<String> {
    if ptr.is_null() {
        return Some(String::new());
    }
    unsafe { CStr::from_ptr(ptr) }
        .to_str()
        .ok()
        .map(|s| s.to_string())
}
