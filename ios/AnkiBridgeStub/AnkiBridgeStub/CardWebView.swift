// Renders an rslib-produced card (HTML + CSS) inside a WKWebView, so the real
// Anki card templating/styling is displayed exactly as the engine emits it.

import SwiftUI
import WebKit

struct CardWebView: UIViewRepresentable {
    /// The card body HTML (question or answer) as rendered by rslib.
    let bodyHTML: String
    /// The card's CSS (from RenderCardResponse.css).
    let css: String

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        webView.loadHTMLString(fullDocument, baseURL: nil)
    }

    /// Full HTML document: a Bauhaus stylesheet + a self-contained JS transform
    /// that reshapes the raw rslib card HTML into the styled structure at load
    /// time (square choice markers, green correct-answer highlight, EXPLANATION
    /// block). If the multiple-choice pattern is absent (a plain front/back
    /// memory card), the transform leaves the body under the base Bauhaus
    /// typography — never crashing, never blanking.
    ///
    /// The incoming deck `css` is appended AFTER our styles so a future deck can
    /// override, and the document `body` is painted paper so it matches the
    /// SwiftUI shell (the WKWebView itself stays clear — see `makeUIView`).
    private var fullDocument: String {
        """
        <!DOCTYPE html>
        <html lang="en">
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
        \(Self.bauhausCSS)
        /* Deck CSS last so a future deck can override the Bauhaus defaults. */
        \(css)
        </style>
        </head>
        <body>
        <!-- The raw rslib HTML is stashed in a hidden template; the transform
             reads it, reshapes it, and writes the styled card into #card.
             If JS is unavailable or the transform throws, the fallback below
             is revealed so content is never lost. -->
        <template id="raw-card">\(bodyHTML)</template>
        <div id="card" class="card"></div>
        <noscript><div class="card fallback">\(bodyHTML)</div></noscript>
        <script>
        \(Self.transformJS)
        </script>
        </body>
        </html>
        """
    }

    // MARK: - Bauhaus stylesheet

    /// The full Bauhaus card stylesheet. Light-only (no `color-scheme` hint),
    /// paper background, ink text/rules, hard edges, no shadows or gradients.
    private static let bauhausCSS = """
    :root {
      /* Exact Bauhaus palette. */
      --red:   #E2231A;
      --yellow:#F2C200;
      --green: #2E9E4F;
      --blue:  #1E52A8;
      --ink:   #1A1A1A;
      --paper: #F5F1E6;
      --futura: "Futura", "Futura-Medium", "Avenir Next", -apple-system, sans-serif;
      /* Pin to light: the paper/ink aesthetic is inherently light-mode. */
      color-scheme: light;
    }

    html { -webkit-text-size-adjust: 100%; }

    body {
      margin: 0;
      padding: 20px 18px 24px;
      background: var(--paper);
      color: var(--ink);
      font-family: var(--futura);
      font-size: 19px;
      line-height: 1.4;
      -webkit-font-smoothing: antialiased;
      /* Never let a legacy deck rule drag us into dark mode. */
      color-scheme: light;
    }

    .card { background: var(--paper); color: var(--ink); }

    /* Question stem: Futura medium. */
    .stem {
      font-weight: 500;
      font-size: 19px;
      line-height: 1.4;
      color: var(--ink);
      margin: 0 0 22px;
    }

    /* Vertical list of multiple-choice options. */
    .choices { display: flex; flex-direction: column; gap: 12px; margin: 0; }

    .choice {
      display: flex;
      align-items: flex-start;
      gap: 14px;
      padding: 4px;
      border: 2.5px solid transparent; /* reserve space so highlight doesn't shift layout */
    }

    /* Hard-edged square letter marker. */
    .marker {
      flex: 0 0 auto;
      width: 30px;
      height: 30px;
      box-sizing: border-box;
      border: 2.5px solid var(--ink);
      background: var(--paper);
      color: var(--ink);
      font-family: var(--futura);
      font-weight: 700;
      font-size: 16px;
      line-height: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      text-transform: uppercase;
    }

    .choice-text {
      font-weight: 500;
      font-size: 18px;
      line-height: 1.35;
      padding-top: 4px;
    }

    /* Green correct-answer treatment: green box, green-filled marker, ANSWER flag. */
    .choice.correct {
      border: 2.5px solid var(--green);
    }
    .choice.correct .marker {
      background: var(--green);
      border-color: var(--green);
      color: var(--paper);
    }
    .answer-flag {
      align-self: flex-start;
      margin-left: auto;
      background: var(--green);
      color: var(--paper);
      font-family: var(--futura);
      font-weight: 700;
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      padding: 4px 8px;
      line-height: 1;
    }

    /* 5px ink rule separating question from the explanation block. */
    .rule {
      border: 0;
      height: 5px;
      background: var(--ink);
      margin: 26px 0 0;
    }

    /* Ink EXPLANATION tab label. */
    .explanation-tab {
      display: inline-block;
      background: var(--ink);
      color: var(--paper);
      font-family: var(--futura);
      font-weight: 700;
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      padding: 6px 12px;
      margin: 0 0 14px;
    }

    .explanation-body {
      font-weight: 400;
      font-size: 18px;
      line-height: 1.55;
      color: var(--ink);
    }
    .explanation-body b, .explanation-body strong { font-weight: 700; }

    /* Fallback path (plain front/back memory card or transform failure). */
    .fallback {
      font-weight: 500;
      font-size: 19px;
      line-height: 1.5;
      color: var(--ink);
    }
    .fallback hr { border: 0; height: 5px; background: var(--ink); margin: 24px 0; }
    .fallback b, .fallback strong { font-weight: 700; }
    """

    // MARK: - Load-time transform

    /// Self-contained JS run on load. It reads the raw card HTML from the
    /// `#raw-card` template, splits it on `<hr id="answer">` into front/back,
    /// pulls out the stem + A–E choices, applies the green correct highlight and
    /// EXPLANATION block when a back exists, and renders the result into `#card`.
    /// Any failure — or a card with no A–E options — falls back to the raw HTML
    /// styled under the base Bauhaus typography.
    private static let transformJS = """
    (function () {
      var raw = document.getElementById('raw-card');
      var out = document.getElementById('card');
      if (!raw || !out) { return; }
      var html = raw.innerHTML;

      // Render the unmodified card HTML under base Bauhaus typography.
      function fallback() {
        out.className = 'card fallback';
        out.innerHTML = html;
      }

      try {
        // 1. Split front / back on the answer rule (back may be absent).
        var parts = html.split(/<hr[^>]*id=["']?answer["']?[^>]*>/i);
        var front = parts[0];
        var back = parts.length > 1 ? parts.slice(1).join('') : null;

        // 2. Split the front on <br> and locate the first A)/A. choice line.
        var choiceRe = /^\\s*([A-E])[).]\\s*(.*)$/;
        var lines = front.split(/<br\\s*\\/?>/i);
        var firstChoiceIdx = -1;
        for (var i = 0; i < lines.length; i++) {
          if (choiceRe.test(stripTags(lines[i]))) { firstChoiceIdx = i; break; }
        }

        // No A–E choices anywhere: this is a plain memory card. Fall back.
        if (firstChoiceIdx === -1) { fallback(); return; }

        // Stem = everything before the first choice line (may be blank).
        var stemHTML = lines.slice(0, firstChoiceIdx).join('<br>').trim();
        // Choices = each subsequent line that matches the choice pattern.
        var choices = [];
        for (var j = firstChoiceIdx; j < lines.length; j++) {
          var m = choiceRe.exec(stripTags(lines[j]));
          if (m) {
            // Preserve the ORIGINAL choice HTML (minus the leading "A)" marker)
            // rather than the tag-stripped/entity-decoded text, so entities like
            // &lt; render literally and there is no decode/re-encode injection
            // path. Matters for inequalities, e.g. a choice authored as "x &lt; 5".
            var choiceHTML = lines[j].replace(/^\\s*(?:<[^>]+>\\s*)*[A-E][).]\\s*/i, '');
            choices.push({ letter: m[1].toUpperCase(), text: choiceHTML.trim() });
          }
        }
        if (choices.length === 0) { fallback(); return; }

        // 3. If a back exists, find the correct letter and the explanation.
        var correct = null;
        var explanationHTML = null;
        if (back) {
          var am = /Answer:\\s*<\\/b>?\\s*([A-E])/i.exec(back);
          if (am) { correct = am[1].toUpperCase(); }
          var em = /Explanation:\\s*<\\/b>?\\s*([\\s\\S]*)$/i.exec(back);
          if (em) { explanationHTML = em[1].trim(); }
        }

        // 4. Build the styled DOM.
        var frag = document.createDocumentFragment();

        if (stemHTML) {
          var stem = document.createElement('div');
          stem.className = 'stem';
          stem.innerHTML = stemHTML;
          frag.appendChild(stem);
        }

        var list = document.createElement('div');
        list.className = 'choices';
        choices.forEach(function (c) {
          var row = document.createElement('div');
          row.className = 'choice' + (correct && c.letter === correct ? ' correct' : '');

          var marker = document.createElement('div');
          marker.className = 'marker';
          marker.textContent = c.letter;
          row.appendChild(marker);

          var txt = document.createElement('div');
          txt.className = 'choice-text';
          txt.innerHTML = c.text;
          row.appendChild(txt);

          if (correct && c.letter === correct) {
            var flag = document.createElement('span');
            flag.className = 'answer-flag';
            flag.textContent = 'Answer';
            row.appendChild(flag);
          }
          list.appendChild(row);
        });
        frag.appendChild(list);

        // Explanation block (only in the answer state).
        if (back) {
          var rule = document.createElement('hr');
          rule.className = 'rule';
          frag.appendChild(rule);

          var tab = document.createElement('div');
          tab.className = 'explanation-tab';
          tab.textContent = 'Explanation';
          frag.appendChild(tab);

          var body = document.createElement('div');
          body.className = 'explanation-body';
          // If we couldn't isolate the explanation text, show the whole back.
          body.innerHTML = (explanationHTML !== null && explanationHTML !== '')
            ? explanationHTML
            : back;
          frag.appendChild(body);
        }

        out.className = 'card';
        out.innerHTML = '';
        out.appendChild(frag);
      } catch (e) {
        // Never crash, never blank: revert to the raw styled body.
        fallback();
      }

      // Strip tags so the choice regex tests visible text, not markup.
      function stripTags(s) {
        var d = document.createElement('div');
        d.innerHTML = s;
        return (d.textContent || d.innerText || '');
      }
    })();
    """
}
