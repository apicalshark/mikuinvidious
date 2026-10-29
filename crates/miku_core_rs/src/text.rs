use std::sync::LazyLock;

use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;

static SEARCH_HIGHLIGHT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)</?em[^>]*>").expect("valid search highlight regex"));

static REPLY_EMOTE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\[([^\[\]\n\r]{1,50})\]").expect("valid reply emote regex"));

const REPLY_EMOTE_DOMAINS: &[&str] = &[
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
];

const REPLY_EMOTE_SIZE1_STYLE: &str =
    "display:inline-block;width:1.4em;height:1.4em;vertical-align:text-bottom;";
const REPLY_EMOTE_SIZE2_STYLE: &str =
    "display:inline-block;width:50px;height:50px;vertical-align:middle;";

/// Escapes HTML characters matching Python's html.escape(s, quote=True) exactly
fn py_html_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(c),
        }
    }
    out
}

#[pyfunction]
pub fn strip_search_highlight(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }

    let stripped = SEARCH_HIGHLIGHT_RE.replace_all(text, "");
    html_escape::decode_html_entities(&stripped).to_string()
}

fn proxied_emote_src(raw_url: &str) -> Option<String> {
    let trimmed = raw_url.trim();
    let owned;
    let url_str = if trimmed.starts_with("//") {
        owned = format!("https:{}", trimmed);
        &owned
    } else {
        trimmed
    };

    let parsed = url::Url::parse(url_str).ok()?;
    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        return None;
    }

    let host = parsed.host_str()?.to_ascii_lowercase();
    if host.is_empty() {
        return None;
    }

    let allowed = REPLY_EMOTE_DOMAINS.iter().any(|&d| {
        let trimmed_d = d.trim_start_matches('.');
        host == trimmed_d || host.ends_with(d)
    });
    if !allowed {
        return None;
    }

    let path = parsed.path();
    for b in path.bytes() {
        if b.is_ascii_whitespace()
            || matches!(
                b,
                b'"' | b'\'' | b'<' | b'>' | b'\\' | b'^' | b'`' | b'{' | b'|' | b'}'
            )
        {
            return None;
        }
    }
    for seg in path.split('/') {
        if seg == ".." {
            return None;
        }
    }

    Some(format!("/proxy/pic/{}{}", host, path))
}

#[pyfunction]
pub fn render_reply_content(message: &str, emote: Option<&Bound<'_, PyDict>>) -> PyResult<String> {
    if message.is_empty() {
        return Ok(String::new());
    }

    let Some(emote_dict) = emote else {
        return Ok(py_html_escape(message));
    };

    let mut out = String::with_capacity(message.len() + 64);
    let mut last_end = 0;

    for m in REPLY_EMOTE_RE.find_iter(message) {
        out.push_str(&py_html_escape(&message[last_end..m.start()]));
        let token = m.as_str();

        let mut replaced = false;
        if let Ok(Some(entry_obj)) = emote_dict.get_item(token) {
            if let Ok(entry) = entry_obj.cast::<PyDict>() {
                let raw_url = entry
                    .get_item("webp_url")
                    .ok()
                    .flatten()
                    .or_else(|| entry.get_item("gif_url").ok().flatten())
                    .or_else(|| entry.get_item("url").ok().flatten())
                    .and_then(|v| v.extract::<String>().ok());

                if let Some(raw) = raw_url {
                    if let Some(src) = proxied_emote_src(&raw) {
                        let size: i64 = match entry.get_item("meta").ok().flatten() {
                            Some(meta_obj) => match meta_obj.cast::<PyDict>() {
                                Ok(meta_dict) => meta_dict
                                    .get_item("size")
                                    .ok()
                                    .flatten()
                                    .and_then(|s| s.extract::<i64>().ok())
                                    .unwrap_or(1),
                                Err(_) => 1,
                            },
                            None => 1,
                        };

                        let style = if size == 2 {
                            REPLY_EMOTE_SIZE2_STYLE
                        } else {
                            REPLY_EMOTE_SIZE1_STYLE
                        };
                        let alt = py_html_escape(token);
                        out.push_str(&format!(
                            "<img src=\"{}\" alt=\"{}\" loading=\"lazy\" style=\"{}\">",
                            src, alt, style
                        ));
                        replaced = true;
                    }
                }
            }
        }

        if !replaced {
            out.push_str(&py_html_escape(token));
        }

        last_end = m.end();
    }

    if last_end < message.len() {
        out.push_str(&py_html_escape(&message[last_end..]));
    }

    Ok(out)
}
