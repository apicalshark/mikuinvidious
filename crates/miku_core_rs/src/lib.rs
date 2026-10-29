use pyo3::prelude::*;

pub mod bvid;
pub mod danmaku;
pub mod mpd;
pub mod text;
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
    m.add_function(wrap_pyfunction!(bvid::bv2av, m)?)?;
    m.add_function(wrap_pyfunction!(bvid::av2bv, m)?)?;
    m.add_function(wrap_pyfunction!(mpd::generate_vod_mpd, m)?)?;
    m.add_function(wrap_pyfunction!(text::strip_search_highlight, m)?)?;
    m.add_function(wrap_pyfunction!(text::render_reply_content, m)?)?;
    Ok(())
}
