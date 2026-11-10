//! Tessera Deck Protocol (TDP) v1 over a serial port or, for the simulator, TCP.
//! The normative description is docs/protocol.md.

use std::io::{self, Read, Write};
use std::net::TcpStream;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const USB_VID: u16 = 0x1209;
pub const USB_PID: u16 = 0x0001;
pub const USB_PRODUCT: &str = "Tessera Deck";

pub const CMD_GET_INFO: u8 = 0x01;
pub const CMD_SET_BRIGHTNESS: u8 = 0x10;
pub const CMD_SET_KEY_IMAGE: u8 = 0x12;
pub const CMD_SET_STRIP_IMAGE: u8 = 0x13;
pub const CMD_FILL: u8 = 0x14;
pub const RSP_ACK: u8 = 0x80;
pub const RSP_INFO: u8 = 0x81;
pub const FMT_JPEG: u8 = 1;
pub const FILL_KEY: u8 = 0;
pub const FILL_ALL: u8 = 3;

const TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Debug)]
pub struct Info {
	pub rows: u8,
	pub cols: u8,
	pub key_w: u16,
	pub key_h: u16,
	pub encoders: u8,
	pub strip_w: u16,
	pub strip_h: u16,
	pub strip_segments: u8,
	pub serial: String,
}

impl Info {
	fn parse(b: &[u8]) -> io::Result<Self> {
		if b.len() < 54 || b[0] != 1 {
			return Err(io::Error::new(io::ErrorKind::InvalidData, "unsupported INFO payload"));
		}
		let u16at = |i: usize| u16::from_le_bytes([b[i], b[i + 1]]);
		let serial = String::from_utf8_lossy(&b[24..40]).trim_end_matches('\0').to_string();
		Ok(Info {
			rows: b[5],
			cols: b[6],
			key_w: u16at(7),
			key_h: u16at(9),
			encoders: b[11],
			strip_w: u16at(12),
			strip_h: u16at(14),
			strip_segments: b[16],
			serial,
		})
	}

	pub fn segment_width(&self) -> u16 {
		if self.strip_segments == 0 { 0 } else { self.strip_w / self.strip_segments as u16 }
	}
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
	Key { key: u8, pressed: bool },
	EncoderRotate { encoder: u8, delta: i8 },
	EncoderButton { encoder: u8, pressed: bool },
	/// kind: 0 tap, 1 long press, 2 drag. Coordinates are strip-relative.
	Touch { kind: u8, x: u16, y: u16 },
	Power { awake: bool },
	/// The link is gone (unplugged, simulator stopped).
	Closed,
}

fn decode_event(ftype: u8, p: &[u8]) -> Option<Event> {
	match (ftype, p.len()) {
		(0xA0, 2..) => Some(Event::Key { key: p[0], pressed: p[1] != 0 }),
		(0xA1, 2..) => Some(Event::EncoderRotate { encoder: p[0], delta: p[1] as i8 }),
		(0xA2, 2..) => Some(Event::EncoderButton { encoder: p[0], pressed: p[1] != 0 }),
		(0xA3, 9..) => Some(Event::Touch {
			kind: p[0],
			x: u16::from_le_bytes([p[1], p[2]]),
			y: u16::from_le_bytes([p[3], p[4]]),
		}),
		(0xA4, 1..) => Some(Event::Power { awake: p[0] != 0 }),
		_ => None,
	}
}

pub fn encode_frame(ftype: u8, seq: u8, payload: &[u8]) -> Vec<u8> {
	let mut f = Vec::with_capacity(payload.len() + 12);
	f.extend_from_slice(b"TD");
	f.push(ftype);
	f.push(seq);
	f.extend_from_slice(&(payload.len() as u32).to_le_bytes());
	f.extend_from_slice(payload);
	let crc = crc32fast::hash(&f[2..]);
	f.extend_from_slice(&crc.to_le_bytes());
	f
}

/// Incremental frame parser for the device-to-host direction (small frames only).
#[derive(Default)]
pub struct Parser {
	buf: Vec<u8>,
}

impl Parser {
	const MAX_PAYLOAD: usize = 4096;

	pub fn feed(&mut self, data: &[u8], mut on_frame: impl FnMut(u8, u8, &[u8])) {
		self.buf.extend_from_slice(data);
		loop {
			let Some(start) = self.buf.windows(2).position(|w| w == b"TD") else {
				let keep = usize::from(self.buf.last() == Some(&b'T'));
				let cut = self.buf.len() - keep;
				self.buf.drain(..cut);
				return;
			};
			self.buf.drain(..start);
			if self.buf.len() < 8 {
				return;
			}
			let len = u32::from_le_bytes([self.buf[4], self.buf[5], self.buf[6], self.buf[7]]) as usize;
			if len > Self::MAX_PAYLOAD {
				self.buf.drain(..2);
				continue;
			}
			let total = 8 + len + 4;
			if self.buf.len() < total {
				return;
			}
			let crc = u32::from_le_bytes([self.buf[8 + len], self.buf[9 + len], self.buf[10 + len], self.buf[11 + len]]);
			if crc != crc32fast::hash(&self.buf[2..8 + len]) {
				self.buf.drain(..2);
				continue;
			}
			on_frame(self.buf[2], self.buf[3], &self.buf[8..8 + len]);
			self.buf.drain(..total);
		}
	}
}

struct Writer {
	port: Box<dyn Write + Send>,
	seq: u8,
	responses: Receiver<(u8, u8, Vec<u8>)>,
}

/// One open deck. Cheap to clone; requests are serialised internally.
#[derive(Clone)]
pub struct Link {
	writer: Arc<Mutex<Writer>>,
	pub info: Info,
}

fn open_port(url: &str) -> io::Result<(Box<dyn Read + Send>, Box<dyn Write + Send>)> {
	if let Some(addr) = url.strip_prefix("tcp://") {
		let stream = TcpStream::connect(addr)?;
		stream.set_nodelay(true)?;
		stream.set_read_timeout(Some(Duration::from_millis(200)))?;
		return Ok((Box::new(stream.try_clone()?), Box::new(stream)));
	}
	let mut port = serialport::new(url, 115_200)
		.timeout(Duration::from_millis(200))
		.open()
		.map_err(io::Error::other)?;
	// DTR tells the deck that a host is attached.
	port.write_data_terminal_ready(true).map_err(io::Error::other)?;
	let reader = port.try_clone().map_err(io::Error::other)?;
	Ok((Box::new(reader), Box::new(port)))
}

fn reader_thread(mut port: Box<dyn Read + Send>, responses: Sender<(u8, u8, Vec<u8>)>, events: Sender<Event>) {
	let mut parser = Parser::default();
	let mut buf = [0u8; 4096];
	loop {
		match port.read(&mut buf) {
			Ok(0) => break,
			Ok(n) => {
				let mut dead = false;
				parser.feed(&buf[..n], |ftype, seq, payload| {
					if ftype >= 0xA0 {
						if let Some(ev) = decode_event(ftype, payload) {
							dead |= events.send(ev).is_err();
						}
					} else {
						dead |= responses.send((ftype, seq, payload.to_vec())).is_err();
					}
				});
				if dead {
					return;
				}
			}
			Err(e) if matches!(e.kind(), io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted) => {
				continue;
			}
			Err(_) => break,
		}
	}
	let _ = events.send(Event::Closed);
}

impl Link {
	/// Opens a deck. `url` is a serial port name or `tcp://host:port`.
	/// Input events (and a final `Event::Closed`) arrive on the returned channel.
	pub fn open(url: &str) -> io::Result<(Link, Receiver<Event>)> {
		let (reader, writer) = open_port(url)?;
		let (resp_tx, resp_rx) = mpsc::channel();
		let (ev_tx, ev_rx) = mpsc::channel();
		std::thread::Builder::new()
			.name("tessera-reader".into())
			.spawn(move || reader_thread(reader, resp_tx, ev_tx))?;
		let writer = Arc::new(Mutex::new(Writer { port: writer, seq: 0, responses: resp_rx }));
		let info = Info::parse(&request_on(&writer, CMD_GET_INFO, &[], RSP_INFO)?)?;
		Ok((Link { writer, info }, ev_rx))
	}

	fn command(&self, cmd: u8, payload: &[u8]) -> io::Result<()> {
		request_on(&self.writer, cmd, payload, RSP_ACK).map(|_| ())
	}

	pub fn set_brightness(&self, percent: u8) -> io::Result<()> {
		self.command(CMD_SET_BRIGHTNESS, &[percent.min(100)])
	}

	/// `jpeg` must be a baseline JPEG of exactly the key size.
	pub fn set_key_jpeg(&self, key: u8, jpeg: &[u8]) -> io::Result<()> {
		let mut p = Vec::with_capacity(jpeg.len() + 2);
		p.extend_from_slice(&[key, FMT_JPEG]);
		p.extend_from_slice(jpeg);
		self.command(CMD_SET_KEY_IMAGE, &p)
	}

	pub fn set_strip_jpeg(&self, x: u16, w: u16, h: u16, jpeg: &[u8]) -> io::Result<()> {
		let mut p = Vec::with_capacity(jpeg.len() + 10);
		p.extend_from_slice(&x.to_le_bytes());
		p.extend_from_slice(&0u16.to_le_bytes());
		p.extend_from_slice(&w.to_le_bytes());
		p.extend_from_slice(&h.to_le_bytes());
		p.extend_from_slice(&[FMT_JPEG, 0]);
		p.extend_from_slice(jpeg);
		self.command(CMD_SET_STRIP_IMAGE, &p)
	}

	pub fn clear_key(&self, key: u8) -> io::Result<()> {
		self.command(CMD_FILL, &[FILL_KEY, key, 0, 0])
	}

	pub fn clear_all(&self) -> io::Result<()> {
		self.command(CMD_FILL, &[FILL_ALL, 0, 0, 0])
	}
}

fn request_on(writer: &Arc<Mutex<Writer>>, cmd: u8, payload: &[u8], expect: u8) -> io::Result<Vec<u8>> {
	let mut w = writer.lock().unwrap_or_else(|e| e.into_inner());
	for _attempt in 0..2 {
		w.seq = w.seq.wrapping_add(1);
		let seq = w.seq;
		while w.responses.try_recv().is_ok() {} // stale answers to timed-out requests
		w.port.write_all(&encode_frame(cmd, seq, payload))?;
		w.port.flush()?;
		loop {
			match w.responses.recv_timeout(TIMEOUT) {
				Ok((ftype, rseq, data)) if rseq == seq => {
					if ftype == RSP_ACK && data.first().copied().unwrap_or(0) != 0 {
						return Err(io::Error::new(io::ErrorKind::InvalidInput, format!("deck refused command 0x{cmd:02x}: status {}", data[0])));
					}
					if ftype != expect {
						return Err(io::Error::new(io::ErrorKind::InvalidData, "unexpected response type"));
					}
					return Ok(data);
				}
				Ok(_) => continue,
				Err(RecvTimeoutError::Timeout) => break,
				Err(RecvTimeoutError::Disconnected) => {
					return Err(io::Error::new(io::ErrorKind::BrokenPipe, "link closed"));
				}
			}
		}
	}
	Err(io::Error::new(io::ErrorKind::TimedOut, format!("no answer to command 0x{cmd:02x}")))
}

/// Serial ports that look like a Tessera Deck.
pub fn find_ports() -> Vec<String> {
	let Ok(ports) = serialport::available_ports() else {
		return Vec::new();
	};
	ports
		.into_iter()
		.filter(|p| match &p.port_type {
			serialport::SerialPortType::UsbPort(usb) => {
				(usb.vid == USB_VID && usb.pid == USB_PID) || usb.product.as_deref().is_some_and(|s| s.contains(USB_PRODUCT))
			}
			_ => false,
		})
		.map(|p| p.port_name)
		.collect()
}

#[cfg(test)]
mod tests {
	use super::*;

	#[test]
	fn frame_round_trip_and_resync() {
		let frame = encode_frame(0xA0, 7, &[3, 1]);
		// CRC matches zlib.crc32 over type..payload
		assert_eq!(&frame[..8], b"TD\xA0\x07\x02\x00\x00\x00");
		let mut stream = b"noise T".to_vec();
		stream.extend_from_slice(&frame);
		let mut bad = frame.clone();
		bad[9] ^= 0xFF;
		stream.extend_from_slice(&bad);
		stream.extend_from_slice(&encode_frame(0xA1, 8, &[2, 0xFF]));

		let mut parser = Parser::default();
		let mut got = Vec::new();
		for chunk in stream.chunks(3) {
			parser.feed(chunk, |t, s, p| got.push((t, s, decode_event(t, p))));
		}
		assert_eq!(
			got,
			vec![
				(0xA0, 7, Some(Event::Key { key: 3, pressed: true })),
				(0xA1, 8, Some(Event::EncoderRotate { encoder: 2, delta: -1 })),
			]
		);
	}
}
