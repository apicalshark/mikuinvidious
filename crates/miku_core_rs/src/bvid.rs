use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

const TABLE: &[u8; 58] = b"FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf";
const BASE: i64 = 58;
const MAX_AID: i64 = 1i64 << 51;
const XOR_CODE: i64 = 23442827791579;
const MASK_CODE: i64 = 2251799813685247;

fn get_char_index(c: u8) -> Option<i64> {
    TABLE.iter().position(|&x| x == c).map(|pos| pos as i64)
}

#[pyfunction]
pub fn bv2av(bvid: &str) -> PyResult<i64> {
    let bytes = bvid.as_bytes();
    if bytes.len() != 12 || &bytes[..2] != b"BV" {
        return Err(PyValueError::new_err(
            "Invalid bvid: must be a 12-character case-sensitive string starting with BV, containing only letters and digits.",
        ));
    }

    // Verify all characters are ASCII alphanumeric
    for &b in bytes {
        if !b.is_ascii_alphanumeric() {
            return Err(PyValueError::new_err(
                "Invalid bvid: must be a 12-character case-sensitive string starting with BV, containing only letters and digits.",
            ));
        }
    }

    let mut arr = [0u8; 12];
    arr.copy_from_slice(bytes);

    // Swap positions
    arr.swap(3, 9);
    arr.swap(4, 7);

    let mut tmp: i64 = 0;
    for &c in &arr[3..] {
        match get_char_index(c) {
            Some(idx) => {
                tmp = tmp.wrapping_mul(BASE).wrapping_add(idx);
            }
            None => {
                return Err(PyValueError::new_err(
                    "Invalid bvid: must be a 12-character case-sensitive string starting with BV, containing only letters and digits.",
                ));
            }
        }
    }

    Ok((tmp & MASK_CODE) ^ XOR_CODE)
}

#[pyfunction]
pub fn av2bv(aid: i64) -> PyResult<String> {
    if aid <= 0 {
        return Err(PyValueError::new_err("aid must be greater than 0."));
    }

    let mut tmp: i64 = (MAX_AID | aid) ^ XOR_CODE;
    let mut r = *b"BV1000000000";
    let mut idx = r.len() - 1;

    while tmp > 0 {
        r[idx] = TABLE[(tmp % BASE) as usize];
        tmp /= BASE;
        if idx == 0 {
            break;
        }
        idx -= 1;
    }

    r.swap(3, 9);
    r.swap(4, 7);

    Ok(String::from_utf8_lossy(&r).into_owned())
}
