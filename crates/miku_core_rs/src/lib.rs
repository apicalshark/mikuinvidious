use pyo3::prelude::*;

pub mod danmaku;

#[pyfunction]
fn is_available() -> bool {
    true
}

#[pymodule]
fn miku_core_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(is_available, m)?)?;
    m.add_function(wrap_pyfunction!(danmaku::parse_danmaku_xml, m)?)?;
    Ok(())
}
