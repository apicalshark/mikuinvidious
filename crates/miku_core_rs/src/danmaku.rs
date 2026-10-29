use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use quick_xml::events::Event;
use quick_xml::Reader;

pub struct DanmakuItem {
    pub text: String,
    pub mode: &'static str,
    pub time: f64,
    pub ftsize: u32,
    pub ftcolor: String,
}

pub fn parse_danmaku_internal(xml_str: &str) -> Result<Vec<DanmakuItem>, quick_xml::Error> {
    let mut reader = Reader::from_str(xml_str);
    reader.config_mut().trim_text(false);

    let mut items = Vec::new();
    let mut in_d = false;
    let mut current_p = String::new();
    let mut current_text = String::new();

    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                if e.local_name().as_ref() == b"d" {
                    in_d = true;
                    current_p.clear();
                    current_text.clear();
                    for attr in e.attributes().flatten() {
                        if attr.key.local_name().as_ref() == b"p" {
                            if let Ok(val) = attr.decode_and_unescape_value(reader.decoder()) {
                                current_p = val.into_owned();
                            }
                            break;
                        }
                    }
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_d {
                    current_text.push_str(&e.unescape()?);
                }
            }
            Ok(Event::CData(ref e)) => {
                if in_d {
                    if let Ok(txt) = std::str::from_utf8(e.as_ref()) {
                        current_text.push_str(txt);
                    }
                }
            }
            Ok(Event::End(ref e)) => {
                if e.local_name().as_ref() == b"d" && in_d {
                    in_d = false;
                    if let Some(item) = process_entry(&current_p, &current_text) {
                        items.push(item);
                    }
                }
            }
            Ok(Event::Eof) => {
                // If EOF occurs while inside <d>, attempt to salvage if we have valid text
                if in_d {
                    if let Some(item) = process_entry(&current_p, &current_text) {
                        items.push(item);
                    }
                }
                break;
            }
            Err(err) => return Err(err),
            _ => {}
        }
        buf.clear();
    }

    Ok(items)
}

fn process_entry(p_str: &str, text: &str) -> Option<DanmakuItem> {
    if text.is_empty() {
        return None;
    }

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

    let ftcolor = match parts.next() {
        Some(s) => match s.trim().parse::<i64>() {
            Ok(c) => format!("{:06x}", c as u32),
            Err(_) => "ffffff".to_string(),
        },
        None => "ffffff".to_string(),
    };

    Some(DanmakuItem {
        text: text.to_string(),
        mode,
        time,
        ftsize,
        ftcolor,
    })
}

#[pyfunction]
pub fn parse_danmaku_xml<'py>(py: Python<'py>, xml_str: &str) -> PyResult<Bound<'py, PyList>> {
    let items = py
        .detach(|| parse_danmaku_internal(xml_str))
        .map_err(|err| PyValueError::new_err(err.to_string()))?;

    let py_list = PyList::empty(py);
    for item in items {
        let py_dict = PyDict::new(py);
        py_dict.set_item("text", item.text)?;
        py_dict.set_item("mode", item.mode)?;
        py_dict.set_item("time", item.time)?;

        let is_black = item.ftcolor == "000000";
        let style = PyDict::new(py);
        let font_size = format!("{}px", item.ftsize);
        let color = format!("#{}", item.ftcolor);
        let font = format!("{}px sans-serif", item.ftsize);

        style.set_item("fontSize", font_size)?;
        style.set_item("color", &color)?;
        if is_black {
            style.set_item(
                "textShadow",
                "-1px -1px #fff, -1px 1px #fff, 1px -1px #fff, 1px 1px #fff",
            )?;
            style.set_item("strokeStyle", "#fff")?;
        } else {
            style.set_item(
                "textShadow",
                "-1px -1px #000, -1px 1px #000, 1px -1px #000, 1px 1px #000",
            )?;
            style.set_item("strokeStyle", "#000")?;
        }
        style.set_item("font", font)?;
        style.set_item("whiteSpace", "pre")?;
        style.set_item("fillStyle", &color)?;
        style.set_item("lineWidth", 2.0)?;

        py_dict.set_item("style", style)?;
        py_list.append(py_dict)?;
    }

    Ok(py_list)
}
