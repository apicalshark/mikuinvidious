use pyo3::prelude::*;

pub mod danmaku;
pub mod wbi;

#[pyfunction]
fn is_available() -> bool {
    true
}

#[pymodule]
fn miku_core_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(is_available, m)?)?;
    m.add_function(wrap_pyfunction!(danmaku::parse_danmaku_xml, m)?)?;
    m.add_function(wrap_pyfunction!(wbi::mixin_from_keys, m)?)?;
    m.add_function(wrap_pyfunction!(wbi::sign_wbi_params, m)?)?;
    Ok(())
}
