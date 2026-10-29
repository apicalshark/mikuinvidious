use md5::{Digest, Md5};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::fmt::Write;
use std::time::{SystemTime, UNIX_EPOCH};

const OE: [usize; 64] = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29,
    28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25,
    54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
];

#[pyfunction]
pub fn mixin_from_keys(img_key: &str, sub_key: &str) -> String {
    let ae = format!("{}{}", img_key, sub_key);
    let ae_bytes = ae.as_bytes();
    let mut res = String::with_capacity(32);
    for &idx in &OE {
        if idx < ae_bytes.len() {
            res.push(ae_bytes[idx] as char);
            if res.len() == 32 {
                break;
            }
        }
    }
    res
}

fn percent_encode(s: &str) -> String {
    let mut encoded = String::with_capacity(s.len());
    for &b in s.as_bytes() {
        match b {
            b'a'..=b'z' | b'A'..=b'Z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                encoded.push(b as char);
            }
            _ => {
                let _ = write!(encoded, "%{:02X}", b);
            }
        }
    }
    encoded
}

#[pyfunction]
#[pyo3(signature = (params, mixin_key, wts=None))]
pub fn sign_wbi_params<'py>(
    _py: Python<'py>,
    params: &Bound<'py, PyDict>,
    mixin_key: &str,
    wts: Option<i64>,
) -> PyResult<Bound<'py, PyDict>> {
    let _ = params.del_item("w_rid");

    let final_wts = match wts {
        Some(t) => t,
        None => SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0),
    };
    params.set_item("wts", final_wts)?;

    match params.get_item("web_location")? {
        Some(val) if !val.is_none() => {}
        _ => {
            params.set_item("web_location", 1315873)?;
        }
    }

    let mut pairs = Vec::with_capacity(params.len());
    for (k, v) in params.iter() {
        let k_str = k.str()?.to_str()?.to_string();
        let v_str = v.str()?.to_str()?.to_string();
        pairs.push((k_str, v_str));
    }

    pairs.sort_by(|a, b| a.0.cmp(&b.0));

    let mut query = String::new();
    for (i, (k, v)) in pairs.iter().enumerate() {
        if i > 0 {
            query.push('&');
        }
        query.push_str(&percent_encode(k));
        query.push('=');
        query.push_str(&percent_encode(v));
    }
    query.push_str(mixin_key);

    let mut hasher = Md5::new();
    hasher.update(query.as_bytes());
    let hash = hasher.finalize();
    let w_rid = format!("{:x}", hash);

    params.set_item("w_rid", w_rid)?;
    Ok(params.clone())
}
