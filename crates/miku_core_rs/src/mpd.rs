use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

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

    // Release GIL during XML string formatting
    let result = py.detach(move || {
        let mut mpd = String::with_capacity(4096);
        mpd.push_str("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
        mpd.push_str("<MPD xmlns=\"urn:mpeg:dash:schema:mpd:2011\" profiles=\"urn:mpeg:dash:profile:isoff-on-demand:2011\" type=\"static\"");
        if duration > 0 {
            mpd.push_str(&format!(" mediaPresentationDuration=\"PT{}S\"", duration));
        }
        mpd.push_str(&format!(" minBufferTime=\"PT{}S\">\n", min_buffer));
        mpd.push_str("  <Period id=\"1\" start=\"PT0S\">\n");

        let mut as_id = 1;
        for (_codecid, codecs_str, tracks) in codec_groups {
            mpd.push_str(&format!(
                "    <AdaptationSet id=\"{}\" contentType=\"video\" mimeType=\"video/mp4\" codecs=\"{}\" segmentAlignment=\"true\" subsegmentAlignment=\"true\" startWithSAP=\"1\">\n",
                as_id, codecs_str
            ));
            for vt in tracks {
                let track_codecs = vt.codecs.as_deref().unwrap_or(&codecs_str);
                let index_exact = if !vt.index_range.is_empty() {
                    " indexRangeExact=\"true\""
                } else {
                    ""
                };
                mpd.push_str(&format!(
                    "      <Representation id=\"video_{}_{}\" codecs=\"{}\" bandwidth=\"{}\" width=\"{}\" height=\"{}\" frameRate=\"{}\">\n",
                    vt.qn, vt.cid, track_codecs, vt.bandwidth, vt.width, vt.height, vt.frame_rate
                ));
                mpd.push_str(&format!(
                    "        <BaseURL>/proxy/dash/{}/{}/video/{}/{}</BaseURL>\n",
                    vid_str, idx, vt.qn, vt.cid
                ));
                mpd.push_str(&format!(
                    "        <SegmentBase indexRange=\"{}\"{}>\n",
                    vt.index_range, index_exact
                ));
                mpd.push_str(&format!(
                    "          <Initialization range=\"{}\"/>\n",
                    vt.init_range
                ));
                mpd.push_str("        </SegmentBase>\n");
                mpd.push_str("      </Representation>\n");
            }
            mpd.push_str("    </AdaptationSet>\n");
            as_id += 1;
        }

        if !audio_tracks.is_empty() {
            mpd.push_str(&format!(
                "    <AdaptationSet id=\"{}\" contentType=\"audio\" mimeType=\"audio/mp4\" segmentAlignment=\"true\" subsegmentAlignment=\"true\" startWithSAP=\"1\">\n",
                as_id
            ));
            for at in audio_tracks {
                let index_exact = if !at.index_range.is_empty() {
                    " indexRangeExact=\"true\""
                } else {
                    ""
                };
                mpd.push_str(&format!(
                    "      <Representation id=\"audio_{}_{}\" codecs=\"{}\" bandwidth=\"{}\">\n",
                    at.qn, at.cid, at.codecs, at.bandwidth
                ));
                mpd.push_str(&format!(
                    "        <BaseURL>/proxy/dash/{}/{}/audio/{}/{}</BaseURL>\n",
                    vid_str, idx, at.qn, at.cid
                ));
                mpd.push_str(&format!(
                    "        <SegmentBase indexRange=\"{}\"{}>\n",
                    at.index_range, index_exact
                ));
                mpd.push_str(&format!(
                    "          <Initialization range=\"{}\"/>\n",
                    at.init_range
                ));
                mpd.push_str("        </SegmentBase>\n");
                mpd.push_str("      </Representation>\n");
            }
            mpd.push_str("    </AdaptationSet>\n");
        }

        mpd.push_str("  </Period>\n");
        mpd.push_str("</MPD>");
        mpd
    });

    Ok(Some(result))
}
