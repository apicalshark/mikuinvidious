use std::io::{Cursor, Read};

use byteorder::{BigEndian, ReadBytesExt};
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

struct PacketHeader {
    packet_length: u32,
    _header_length: u16,
    protocol_version: u16,
    datapack_type: u32,
    _sequence_id: u32,
}

fn read_header(rdr: &mut Cursor<&[u8]>) -> Option<PacketHeader> {
    let packet_length = rdr.read_u32::<BigEndian>().ok()?;
    let _header_length = rdr.read_u16::<BigEndian>().ok()?;
    let protocol_version = rdr.read_u16::<BigEndian>().ok()?;
    let datapack_type = rdr.read_u32::<BigEndian>().ok()?;
    let _sequence_id = rdr.read_u32::<BigEndian>().ok()?;
    Some(PacketHeader {
        packet_length,
        _header_length,
        protocol_version,
        datapack_type,
        _sequence_id,
    })
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

    let mut rdr = Cursor::new(data);
    let Some(header) = read_header(&mut rdr) else {
        return Vec::new();
    };

    if header.protocol_version == PROTOCOL_VERSION_HEARTBEAT
        && header.datapack_type == DATAPACK_TYPE_HEARTBEAT_RESPONSE
    {
        let view = rdr.read_u32::<BigEndian>().unwrap_or(0);
        return vec![ParsedPacket {
            protocol_version: header.protocol_version,
            datapack_type: header.datapack_type,
            data: PacketData::View(view),
        }];
    }

    let content = if header.protocol_version == PROTOCOL_VERSION_BROTLI_JSON {
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
        let mut inner_rdr = Cursor::new(&content[offset..]);
        let Some(inner_hdr) = read_header(&mut inner_rdr) else {
            break;
        };

        let inner_len = inner_hdr.packet_length as usize;
        if inner_len <= 16 || offset + inner_len > content.len() {
            break;
        }

        let chunk = &content[offset + 16..offset + inner_len];

        let pkt_data = if inner_hdr.datapack_type == DATAPACK_TYPE_HEARTBEAT_RESPONSE {
            let mut view_rdr = Cursor::new(chunk);
            let view = match view_rdr.read_u32::<BigEndian>() {
                Ok(v) => v,
                Err(_) => break,
            };
            PacketData::View(view)
        } else {
            match serde_json::from_slice::<serde_json::Value>(chunk) {
                Ok(v) => PacketData::Json(v),
                Err(_) => {
                    let s = String::from_utf8_lossy(chunk);
                    match serde_json::from_str::<serde_json::Value>(&s) {
                        Ok(v) => PacketData::Json(v),
                        Err(_) => PacketData::None,
                    }
                }
            }
        };

        packets.push(ParsedPacket {
            protocol_version: inner_hdr.protocol_version,
            datapack_type: inner_hdr.datapack_type,
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
