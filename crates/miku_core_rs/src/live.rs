use std::io::Read;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

const PROTOCOL_VERSION_HEARTBEAT: u16 = 1;
const PROTOCOL_VERSION_BROTLI_JSON: u16 = 3;

const DATAPACK_TYPE_HEARTBEAT_RESPONSE: u32 = 3;

enum PacketData {
    View(u32),
    Json(serde_json::Value),
    None,
}

struct ParsedPacket {
    protocol_version: u16,
    datapack_type: u32,
    data: PacketData,
}

fn json_to_py<'py>(py: Python<'py>, val: &serde_json::Value) -> PyResult<Bound<'py, PyAny>> {
    match val {
        serde_json::Value::Null => Ok(py.None().into_bound(py)),
        serde_json::Value::Bool(b) => Ok(b.into_pyobject(py)?.to_owned().into_any()),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_pyobject(py)?.to_owned().into_any())
            } else if let Some(u) = n.as_u64() {
                Ok(u.into_pyobject(py)?.to_owned().into_any())
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_pyobject(py)?.to_owned().into_any())
            } else {
                Ok(py.None().into_bound(py))
            }
        }
        serde_json::Value::String(s) => Ok(s.into_pyobject(py)?.to_owned().into_any()),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(json_to_py(py, item)?)?;
            }
            Ok(list.into_any())
        }
        serde_json::Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_to_py(py, v)?)?;
            }
            Ok(dict.into_any())
        }
    }
}

fn parse_packets_internal(data: &[u8]) -> Vec<ParsedPacket> {
    if data.len() < 16 {
        return Vec::new();
    }

    let protover = u16::from_be_bytes([data[6], data[7]]);
    let datapack_type = u32::from_be_bytes([data[8], data[9], data[10], data[11]]);

    if protover == PROTOCOL_VERSION_HEARTBEAT && datapack_type == DATAPACK_TYPE_HEARTBEAT_RESPONSE {
        let view = if data.len() >= 20 {
            u32::from_be_bytes([data[16], data[17], data[18], data[19]])
        } else {
            0
        };
        return vec![ParsedPacket {
            protocol_version: protover,
            datapack_type,
            data: PacketData::View(view),
        }];
    }

    let content = if protover == PROTOCOL_VERSION_BROTLI_JSON {
        let mut decompressed = Vec::with_capacity(data.len() * 4);
        let mut reader = brotli::Decompressor::new(&data[16..], 4096);
        match reader.read_to_end(&mut decompressed) {
            Ok(_) => decompressed,
            Err(_) => data[16..].to_vec(),
        }
    } else {
        data.to_vec()
    };

    let mut packets = Vec::new();
    let mut offset = 0;
    while offset + 16 <= content.len() {
        let inner_len = u32::from_be_bytes([
            content[offset],
            content[offset + 1],
            content[offset + 2],
            content[offset + 3],
        ]) as usize;

        if inner_len <= 16 || offset + inner_len > content.len() {
            break;
        }

        let inner_proto = u16::from_be_bytes([content[offset + 6], content[offset + 7]]);
        let inner_type = u32::from_be_bytes([
            content[offset + 8],
            content[offset + 9],
            content[offset + 10],
            content[offset + 11],
        ]);
        let chunk = &content[offset + 16..offset + inner_len];

        let pkt_data = if inner_type == DATAPACK_TYPE_HEARTBEAT_RESPONSE {
            if chunk.len() < 4 {
                break;
            }
            let view = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
            PacketData::View(view)
        } else {
            match serde_json::from_slice::<serde_json::Value>(chunk) {
                Ok(v) => PacketData::Json(v),
                Err(_) => {
                    // Try lossy utf-8
                    let s = String::from_utf8_lossy(chunk);
                    match serde_json::from_str::<serde_json::Value>(&s) {
                        Ok(v) => PacketData::Json(v),
                        Err(_) => PacketData::None,
                    }
                }
            }
        };

        packets.push(ParsedPacket {
            protocol_version: inner_proto,
            datapack_type: inner_type,
            data: pkt_data,
        });

        offset += inner_len;
    }

    packets
}

#[pyfunction]
pub fn unpack_live_danmaku<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyList>> {
    let packets = py.detach(|| parse_packets_internal(data));

    let out_list = PyList::empty(py);
    for pkt in packets {
        let dict = PyDict::new(py);
        dict.set_item("protocol_version", pkt.protocol_version)?;
        dict.set_item("datapack_type", pkt.datapack_type)?;

        match pkt.data {
            PacketData::View(view) => {
                let vdict = PyDict::new(py);
                vdict.set_item("view", view)?;
                dict.set_item("data", vdict)?;
            }
            PacketData::Json(ref j) => {
                dict.set_item("data", json_to_py(py, j)?)?;
            }
            PacketData::None => {
                dict.set_item("data", py.None())?;
            }
        }
        out_list.append(dict)?;
    }

    Ok(out_list)
}
