use pyo3::prelude::*;

#[pyfunction]
fn is_available() -> bool {
    true
}

#[pymodule]
fn miku_core_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(is_available, m)?)?;
    Ok(())
}
