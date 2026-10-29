use pyo3::exceptions::PyValueError;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use quick_xml::escape::{resolve_xml_entity, unescape};
use quick_xml::events::Event;
use quick_xml::Reader;

#[derive(Clone, Copy)]
struct ParsedP {
    time: f64,
    mode: &'static str,
    ftsize: u32,
    ftcolor: [u8; 6],
}

fn parse_p_attr(p_str: &str) -> Option<ParsedP> {
    let mut parts = p_str.split(',');
    let time_str = parts.next()?.trim();
    let mode_str = parts.next()?.trim();

    let mode = match mode_str {
        "1" => "rtl",
        "4" => "bottom",
        "5" => "top",
        "6" => "ltr",
        "7" => "rtl",
        "8" => "rtl",
        _ => return None,
    };

    let time: f64 = time_str.parse().unwrap_or(0.0);

    let ftsize: u32 = match parts.next() {
        Some(s) => match s.trim().parse::<u32>() {
            Ok(v) if v > 0 => v,
            _ => 25,
        },
        None => 25,
    };

    let mut ftcolor = *b"ffffff";
    if let Some(s) = parts.next() {
        if let Ok(c) = s.trim().parse::<i64>() {
            let cu = c as u32;
            const HEX_DIGITS: &[u8; 16] = b"0123456789abcdef";
            ftcolor[0] = HEX_DIGITS[((cu >> 20) & 0xf) as usize];
            ftcolor[1] = HEX_DIGITS[((cu >> 16) & 0xf) as usize];
            ftcolor[2] = HEX_DIGITS[((cu >> 12) & 0xf) as usize];
            ftcolor[3] = HEX_DIGITS[((cu >> 8) & 0xf) as usize];
            ftcolor[4] = HEX_DIGITS[((cu >> 4) & 0xf) as usize];
            ftcolor[5] = HEX_DIGITS[(cu & 0xf) as usize];
        }
    }

    Some(ParsedP {
        time,
        mode,
        ftsize,
        ftcolor,
    })
}

pub struct DanmakuItem {
    pub text: String,
    pub mode: &'static str,
    pub time: f64,
    pub ftsize: u32,
    pub ftcolor: [u8; 6],
}

pub fn parse_danmaku_internal(xml_str: &str) -> Result<Vec<DanmakuItem>, quick_xml::Error> {
    let mut reader = Reader::from_str(xml_str);
    reader.config_mut().trim_text(false);

    let mut items = Vec::new();
    let mut in_d = false;
    let mut current_p: Option<ParsedP> = None;
    let mut current_text = String::with_capacity(128);

    loop {
        match reader.read_event() {
            Ok(Event::Start(ref e)) => {
                if e.local_name().as_ref() == "d" {
                    in_d = true;
                    current_p = None;
                    current_text.clear();
                    for attr in e.attributes().flatten() {
                        if attr.key.local_name().as_ref() == "p" {
                            if let Ok(val) =
                                attr.normalized_value(quick_xml::XmlVersion::Implicit1_0)
                            {
                                current_p = parse_p_attr(&val);
                            }
                            break;
                        }
                    }
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_d {
                    current_text.push_str(&unescape(e.as_ref())?);
                }
            }
            Ok(Event::GeneralRef(ref e)) => {
                if in_d {
                    if let Some(s) = resolve_xml_entity(e.as_ref()) {
                        current_text.push_str(s);
                    } else if let Ok(Some(ch)) = e.resolve_char_ref() {
                        current_text.push(ch);
                    } else {
                        return Err(quick_xml::Error::Escape(
                            quick_xml::escape::EscapeError::UnrecognizedEntity(0..0, e.to_string()),
                        ));
                    }
                }
            }
            Ok(Event::CData(ref e)) => {
                if in_d {
                    current_text.push_str(e.as_ref());
                }
            }
            Ok(Event::End(ref e)) => {
                if e.local_name().as_ref() == "d" && in_d {
                    in_d = false;
                    if !current_text.is_empty() {
                        if let Some(p) = current_p.take() {
                            items.push(DanmakuItem {
                                text: std::mem::take(&mut current_text),
                                mode: p.mode,
                                time: p.time,
                                ftsize: p.ftsize,
                                ftcolor: p.ftcolor,
                            });
                        }
                    }
                }
            }
            Ok(Event::Eof) => {
                // If EOF occurs while inside <d>, salvage if we have valid text
                if in_d && !current_text.is_empty() {
                    if let Some(p) = current_p.take() {
                        items.push(DanmakuItem {
                            text: std::mem::take(&mut current_text),
                            mode: p.mode,
                            time: p.time,
                            ftsize: p.ftsize,
                            ftcolor: p.ftcolor,
                        });
                    }
                }
                break;
            }
            Err(err) => return Err(err),
            _ => {}
        }
    }

    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_entities() {
        let xml = r#"<i><d p="35.00,1,25,16777215">&lt;tag&gt; &amp; &quot;quote&quot; &#x3c;&#62;</d></i>"#;
        let items = parse_danmaku_internal(xml).expect("parse failed");
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].text, "<tag> & \"quote\" <>");
    }

    #[test]
    fn test_unknown_entity_error() {
        let xml = r#"<i><d p="35.00,1,25,16777215">&unknown;</d></i>"#;
        assert!(parse_danmaku_internal(xml).is_err());
    }
}

#[pyfunction]
pub fn parse_danmaku_xml<'py>(py: Python<'py>, xml_str: &str) -> PyResult<Bound<'py, PyList>> {
    let items = py
        .detach(|| parse_danmaku_internal(xml_str))
        .map_err(|err| PyValueError::new_err(err.to_string()))?;

    let k_text = intern!(py, "text");
    let k_mode = intern!(py, "mode");
    let k_time = intern!(py, "time");
    let k_style = intern!(py, "style");
    let k_font_size = intern!(py, "fontSize");
    let k_color = intern!(py, "color");
    let k_text_shadow = intern!(py, "textShadow");
    let k_stroke_style = intern!(py, "strokeStyle");
    let k_font = intern!(py, "font");
    let k_white_space = intern!(py, "whiteSpace");
    let k_fill_style = intern!(py, "fillStyle");
    let k_line_width = intern!(py, "lineWidth");
    let v_pre = intern!(py, "pre");
    let v_stroke_white = intern!(py, "#fff");
    let v_stroke_black = intern!(py, "#000");
    let v_shadow_black = intern!(
        py,
        "-1px -1px #fff, -1px 1px #fff, 1px -1px #fff, 1px 1px #fff"
    );
    let v_shadow_white = intern!(
        py,
        "-1px -1px #000, -1px 1px #000, 1px -1px #000, 1px 1px #000"
    );

    let py_list = PyList::empty(py);
    for item in items {
        let py_dict = PyDict::new(py);
        py_dict.set_item(k_text, item.text)?;
        py_dict.set_item(k_mode, item.mode)?;
        py_dict.set_item(k_time, item.time)?;

        let is_black = item.ftcolor == *b"000000";
        let style = PyDict::new(py);
        let color_hex = std::str::from_utf8(&item.ftcolor).unwrap_or("ffffff");
        let font_size = format!("{}px", item.ftsize);
        let color = format!("#{}", color_hex);
        let font = format!("{}px sans-serif", item.ftsize);

        style.set_item(k_font_size, font_size)?;
        style.set_item(k_color, &color)?;
        if is_black {
            style.set_item(k_text_shadow, v_shadow_black)?;
            style.set_item(k_stroke_style, v_stroke_white)?;
        } else {
            style.set_item(k_text_shadow, v_shadow_white)?;
            style.set_item(k_stroke_style, v_stroke_black)?;
        }
        style.set_item(k_font, font)?;
        style.set_item(k_white_space, v_pre)?;
        style.set_item(k_fill_style, &color)?;
        style.set_item(k_line_width, 2.0)?;

        py_dict.set_item(k_style, style)?;
        py_list.append(py_dict)?;
    }

    Ok(py_list)
}
