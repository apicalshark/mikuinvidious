use pyo3::prelude::*;
use pyo3::types::PyDict;

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

    let mut cleaned = String::with_capacity(text.len());
    let mut rest = text;

    while let Some(start_idx) = rest.find('<') {
        cleaned.push_str(&rest[..start_idx]);
        let after_lt = &rest[start_idx..];
        let bytes = after_lt.as_bytes();

        let is_em = (after_lt.starts_with("<em")
            && (bytes.len() == 3 || bytes[3] == b'>' || bytes[3].is_ascii_whitespace()))
            || (after_lt.starts_with("</em")
                && (bytes.len() == 4 || bytes[4] == b'>' || bytes[4].is_ascii_whitespace()));

        if is_em {
            if let Some(close_idx) = after_lt.find('>') {
                rest = &after_lt[close_idx + 1..];
                continue;
            }
        }

        cleaned.push('<');
        rest = &after_lt[1..];
    }
    cleaned.push_str(rest);

    html_escape::decode_html_entities(&cleaned).to_string()
}

fn proxied_emote_src(raw_url: &str) -> Option<String> {
    let mut url = raw_url.trim();
    let owned;
    if url.starts_with("//") {
        owned = format!("https:{}", url);
        url = &owned;
    }

    let scheme_end = url.find("://")?;
    let scheme = &url[..scheme_end].to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return None;
    }

    let after_scheme = &url[scheme_end + 3..];
    let (authority, path_with_query) = match after_scheme.find('/') {
        Some(pos) => (&after_scheme[..pos], &after_scheme[pos..]),
        None => (after_scheme, "/"),
    };

    let host = authority.split(':').next()?.to_ascii_lowercase();
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

    let path = match path_with_query.find(['?', '#']) {
        Some(pos) => &path_with_query[..pos],
        None => path_with_query,
    };

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
    let mut pos = 0;

    while let Some(rel_open) = message[pos..].find('[') {
        let open_idx = pos + rel_open;
        let after_open = &message[open_idx + 1..];

        if let Some(rel_close) = after_open.find(']') {
            if rel_close > 0 && rel_close <= 50 {
                let inner = &after_open[..rel_close];
                if !inner.contains('[') && !inner.contains('\n') && !inner.contains('\r') {
                    let close_idx = open_idx + 1 + rel_close;
                    let token = &message[open_idx..=close_idx];

                    out.push_str(&py_html_escape(&message[pos..open_idx]));

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

                    pos = close_idx + 1;
                    continue;
                }
            }
        }

        out.push_str(&py_html_escape(&message[pos..=open_idx]));
        pos = open_idx + 1;
    }

    if pos < message.len() {
        out.push_str(&py_html_escape(&message[pos..]));
    }

    Ok(out)
}
