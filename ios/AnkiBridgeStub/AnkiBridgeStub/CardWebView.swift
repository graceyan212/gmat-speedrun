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

    /// Wrap the card in the standard Anki card scaffold (`.card` class + style),
    /// which is what Anki's own reviewer does before injecting the template HTML.
    private var fullDocument: String {
        """
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
        :root { color-scheme: light dark; }
        body { margin: 16px; font: -apple-system-body; }
        \(css)
        </style>
        </head>
        <body>
        <div class="card">
        \(bodyHTML)
        </div>
        </body>
        </html>
        """
    }
}
