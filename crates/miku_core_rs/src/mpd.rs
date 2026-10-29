use std::io::Cursor;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use quick_xml::events::{BytesEnd, BytesStart, BytesText, Event};
use quick_xml::writer::Writer;

fn extract_init_range(sb: &Bound<'_, PyDict>) -> String {
    let init = sb
        .get_item("Initialization")
        .ok()
        .flatten()
        .or_else(|| sb.get_item("initialization").ok().flatten());

    if let Some(init_obj) = init {
        if let Ok(init_dict) = init_obj.cast::<PyDict>() {
            if let Some(r) = init_dict
                .get_item("range")
                .ok()
                .flatten()
                .or_else(|| init_dict.get_item("indexRange").ok().flatten())
            {
                if let Ok(s) = r.extract::<String>() {
                    if !s.is_empty() {
                        return s;
                    }
                }
            }
            return "0-999".to_string();
        } else if let Ok(s) = init_obj.extract::<String>() {
            if !s.is_empty() {
                return s;
            }
        }
    }
    "0-999".to_string()
}

struct VideoTrackData {
    qn: i64,
    cid: i64,
    bandwidth: i64,
    width: i64,
    height: i64,
    frame_rate: String,
    codecs: Option<String>,
    init_range: String,
    index_range: String,
}

struct AudioTrackData {
    qn: i64,
    cid: i64,
    bandwidth: i64,
    codecs: String,
    init_range: String,
    index_range: String,
}

#[pyfunction]
pub fn generate_vod_mpd(
    py: Python<'_>,
    vid: &str,
    idx: i64,
    dash_data: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let Ok(dash_dict) = dash_data.cast::<PyDict>() else {
        return Ok(None);
    };

    let Some(dash_val) = dash_dict.get_item("dash")? else {
        return Ok(None);
    };

    let Ok(dash) = dash_val.cast::<PyDict>() else {
        return Ok(None);
    };

    if dash.is_empty() {
        return Ok(None);
    }

    let duration: i64 = dash
        .get_item("duration")?
        .and_then(|v| v.extract::<i64>().ok())
        .unwrap_or(0);

    let min_buffer: f64 = dash
        .get_item("minBufferTime")?
        .and_then(|v| v.extract::<f64>().ok())
        .unwrap_or(1.5);

    // Parse video tracks
    let mut codec_groups: Vec<(i64, String, Vec<VideoTrackData>)> = Vec::new();
    if let Some(video_val) = dash.get_item("video")? {
        if let Ok(video_list) = video_val.cast::<PyList>() {
            for item in video_list {
                let Ok(track) = item.cast::<PyDict>() else {
                    continue;
                };

                let Some(qn_obj) = track.get_item("id")? else {
                    continue;
                };
                let Ok(qn) = qn_obj.extract::<i64>() else {
                    continue;
                };

                let Some(sb_obj) = track.get_item("SegmentBase")? else {
                    continue;
                };
                let Ok(sb) = sb_obj.cast::<PyDict>() else {
                    continue;
                };

                let index_range: String = sb
                    .get_item("indexRange")?
                    .and_then(|v| v.extract::<String>().ok())
                    .unwrap_or_default();

                if index_range.is_empty() {
                    continue;
                }

                let init_range = extract_init_range(sb);
                let cid: i64 = track
                    .get_item("codecid")?
                    .and_then(|v| v.extract::<i64>().ok())
                    .unwrap_or(0);
                let bandwidth: i64 = track
                    .get_item("bandwidth")?
                    .and_then(|v| v.extract::<i64>().ok())
                    .unwrap_or(0);
                let width: i64 = track
                    .get_item("width")?
                    .and_then(|v| v.extract::<i64>().ok())
                    .unwrap_or(0);
                let height: i64 = track
                    .get_item("height")?
                    .and_then(|v| v.extract::<i64>().ok())
                    .unwrap_or(0);
                let frame_rate: String = track
                    .get_item("frameRate")?
                    .or_else(|| track.get_item("frame_rate").ok().flatten())
                    .and_then(|v| v.extract::<String>().ok())
                    .unwrap_or_else(|| "24".to_string());
                let codecs: Option<String> = track
                    .get_item("codecs")?
                    .and_then(|v| v.extract::<String>().ok());

                let vtrack = VideoTrackData {
                    qn,
                    cid,
                    bandwidth,
                    width,
                    height,
                    frame_rate,
                    codecs,
                    init_range,
                    index_range,
                };

                if let Some(group) = codec_groups.iter_mut().find(|(c, _, _)| *c == cid) {
                    group.2.push(vtrack);
                } else {
                    let default_codecs = vtrack
                        .codecs
                        .clone()
                        .unwrap_or_else(|| "avc1.64001F".to_string());
                    codec_groups.push((cid, default_codecs, vec![vtrack]));
                }
            }
        }
    }

    // Parse audio tracks
    let mut audio_tracks: Vec<AudioTrackData> = Vec::new();
    let mut audio_list_val = dash.get_item("audio")?;

    if audio_list_val.as_ref().map(|v| v.is_none()).unwrap_or(true)
        || audio_list_val
            .as_ref()
            .and_then(|v| v.cast::<PyList>().ok())
            .map(|l| l.is_empty())
            .unwrap_or(false)
    {
        if let Some(dolby) = dash.get_item("dolby")? {
            if let Ok(dolby_dict) = dolby.cast::<PyDict>() {
                audio_list_val = dolby_dict.get_item("audio")?;
            }
        }
    }

    if audio_list_val.as_ref().map(|v| v.is_none()).unwrap_or(true)
        || audio_list_val
            .as_ref()
            .and_then(|v| v.cast::<PyList>().ok())
            .map(|l| l.is_empty())
            .unwrap_or(false)
    {
        if let Some(flac) = dash.get_item("flac")? {
            if let Ok(flac_dict) = flac.cast::<PyDict>() {
                audio_list_val = flac_dict.get_item("audio")?;
            }
        }
    }

    if let Some(audio_val) = audio_list_val {
        if let Ok(audio_list) = audio_val.cast::<PyList>() {
            for item in audio_list {
                let Ok(track) = item.cast::<PyDict>() else {
                    continue;
                };

                let Some(qn_obj) = track.get_item("id")? else {
                    continue;
                };
                let Ok(qn) = qn_obj.extract::<i64>() else {
                    continue;
                };

                let Some(sb_obj) = track.get_item("SegmentBase")? else {
                    continue;
                };
                let Ok(sb) = sb_obj.cast::<PyDict>() else {
                    continue;
                };

                let index_range: String = sb
                    .get_item("indexRange")?
                    .and_then(|v| v.extract::<String>().ok())
                    .unwrap_or_default();

                if index_range.is_empty() {
                    continue;
                }

                let init_range = extract_init_range(sb);
                let cid: i64 = track
                    .get_item("codecid")?
                    .and_then(|v| v.extract::<i64>().ok())
                    .unwrap_or(0);
                let bandwidth: i64 = track
                    .get_item("bandwidth")?
                    .and_then(|v| v.extract::<i64>().ok())
                    .unwrap_or(0);
                let codecs: String = track
                    .get_item("codecs")?
                    .and_then(|v| v.extract::<String>().ok())
                    .unwrap_or_else(|| "mp4a.40.2".to_string());

                audio_tracks.push(AudioTrackData {
                    qn,
                    cid,
                    bandwidth,
                    codecs,
                    init_range,
                    index_range,
                });
            }
        }
    }

    let vid_str = vid.to_string();

    // Release GIL during structured XML writing
    let result = py.detach(move || -> String {
        let mut writer = Writer::new_with_indent(Cursor::new(Vec::with_capacity(4096)), b' ', 2);

        // <MPD ...>
        let mut mpd_start = BytesStart::new("MPD");
        mpd_start.push_attribute(("xmlns", "urn:mpeg:dash:schema:mpd:2011"));
        mpd_start.push_attribute(("profiles", "urn:mpeg:dash:profile:isoff-on-demand:2011"));
        mpd_start.push_attribute(("type", "static"));
        let duration_str = format!("PT{}S", duration);
        if duration > 0 {
            mpd_start.push_attribute(("mediaPresentationDuration", duration_str.as_str()));
        }
        let min_buffer_str = format!("PT{}S", min_buffer);
        mpd_start.push_attribute(("minBufferTime", min_buffer_str.as_str()));
        let _ = writer.write_event(Event::Start(mpd_start));

        // <Period id="1" start="PT0S">
        let mut period_start = BytesStart::new("Period");
        period_start.push_attribute(("id", "1"));
        period_start.push_attribute(("start", "PT0S"));
        let _ = writer.write_event(Event::Start(period_start));

        let mut as_id = 1;
        for (_codecid, codecs_str, tracks) in codec_groups {
            let mut as_start = BytesStart::new("AdaptationSet");
            let as_id_str = as_id.to_string();
            as_start.push_attribute(("id", as_id_str.as_str()));
            as_start.push_attribute(("contentType", "video"));
            as_start.push_attribute(("mimeType", "video/mp4"));
            as_start.push_attribute(("codecs", codecs_str.as_str()));
            as_start.push_attribute(("segmentAlignment", "true"));
            as_start.push_attribute(("subsegmentAlignment", "true"));
            as_start.push_attribute(("startWithSAP", "1"));
            let _ = writer.write_event(Event::Start(as_start));

            for vt in tracks {
                let track_codecs = vt.codecs.as_deref().unwrap_or(codecs_str.as_str());
                let mut rep_start = BytesStart::new("Representation");
                let rep_id = format!("video_{}_{}", vt.qn, vt.cid);
                rep_start.push_attribute(("id", rep_id.as_str()));
                rep_start.push_attribute(("codecs", track_codecs));
                let bw_str = vt.bandwidth.to_string();
                rep_start.push_attribute(("bandwidth", bw_str.as_str()));
                let w_str = vt.width.to_string();
                rep_start.push_attribute(("width", w_str.as_str()));
                let h_str = vt.height.to_string();
                rep_start.push_attribute(("height", h_str.as_str()));
                rep_start.push_attribute(("frameRate", vt.frame_rate.as_str()));
                let _ = writer.write_event(Event::Start(rep_start));

                // <BaseURL>
                let base_url =
                    format!("/proxy/dash/{}/{}/video/{}/{}", vid_str, idx, vt.qn, vt.cid);
                let _ = writer.write_event(Event::Start(BytesStart::new("BaseURL")));
                let _ = writer.write_event(Event::Text(BytesText::new(&base_url)));
                let _ = writer.write_event(Event::End(BytesEnd::new("BaseURL")));

                // <SegmentBase>
                let mut sb_start = BytesStart::new("SegmentBase");
                sb_start.push_attribute(("indexRange", vt.index_range.as_str()));
                if !vt.index_range.is_empty() {
                    sb_start.push_attribute(("indexRangeExact", "true"));
                }
                let _ = writer.write_event(Event::Start(sb_start));

                // <Initialization>
                let mut init_start = BytesStart::new("Initialization");
                init_start.push_attribute(("range", vt.init_range.as_str()));
                let _ = writer.write_event(Event::Empty(init_start));

                let _ = writer.write_event(Event::End(BytesEnd::new("SegmentBase")));
                let _ = writer.write_event(Event::End(BytesEnd::new("Representation")));
            }

            let _ = writer.write_event(Event::End(BytesEnd::new("AdaptationSet")));
            as_id += 1;
        }

        if !audio_tracks.is_empty() {
            let mut as_start = BytesStart::new("AdaptationSet");
            let as_id_str = as_id.to_string();
            as_start.push_attribute(("id", as_id_str.as_str()));
            as_start.push_attribute(("contentType", "audio"));
            as_start.push_attribute(("mimeType", "audio/mp4"));
            as_start.push_attribute(("segmentAlignment", "true"));
            as_start.push_attribute(("subsegmentAlignment", "true"));
            as_start.push_attribute(("startWithSAP", "1"));
            let _ = writer.write_event(Event::Start(as_start));

            for at in audio_tracks {
                let mut rep_start = BytesStart::new("Representation");
                let rep_id = format!("audio_{}_{}", at.qn, at.cid);
                rep_start.push_attribute(("id", rep_id.as_str()));
                rep_start.push_attribute(("codecs", at.codecs.as_str()));
                let bw_str = at.bandwidth.to_string();
                rep_start.push_attribute(("bandwidth", bw_str.as_str()));
                let _ = writer.write_event(Event::Start(rep_start));

                // <BaseURL>
                let base_url =
                    format!("/proxy/dash/{}/{}/audio/{}/{}", vid_str, idx, at.qn, at.cid);
                let _ = writer.write_event(Event::Start(BytesStart::new("BaseURL")));
                let _ = writer.write_event(Event::Text(BytesText::new(&base_url)));
                let _ = writer.write_event(Event::End(BytesEnd::new("BaseURL")));

                // <SegmentBase>
                let mut sb_start = BytesStart::new("SegmentBase");
                sb_start.push_attribute(("indexRange", at.index_range.as_str()));
                if !at.index_range.is_empty() {
                    sb_start.push_attribute(("indexRangeExact", "true"));
                }
                let _ = writer.write_event(Event::Start(sb_start));

                // <Initialization>
                let mut init_start = BytesStart::new("Initialization");
                init_start.push_attribute(("range", at.init_range.as_str()));
                let _ = writer.write_event(Event::Empty(init_start));

                let _ = writer.write_event(Event::End(BytesEnd::new("SegmentBase")));
                let _ = writer.write_event(Event::End(BytesEnd::new("Representation")));
            }

            let _ = writer.write_event(Event::End(BytesEnd::new("AdaptationSet")));
        }

        let _ = writer.write_event(Event::End(BytesEnd::new("Period")));
        let _ = writer.write_event(Event::End(BytesEnd::new("MPD")));

        let inner = writer.into_inner().into_inner();
        let xml_str = String::from_utf8(inner).unwrap_or_default();
        format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n{}", xml_str)
    });

    Ok(Some(result))
}
