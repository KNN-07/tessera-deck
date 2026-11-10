//! OpenDeck device plugin for the Tessera Deck.
//!
//! OpenDeck owns profiles, pages, actions and every Stream Deck SDK plugin; this
//! plugin only makes a Tessera Deck appear as one more device: it forwards key,
//! dial and touch input to OpenDeck and puts the images OpenDeck renders on the
//! deck's keys and touch strip.
//!
//! Decks are found by USB ID. For the simulator (or an unusual port name) set
//! TESSERA_DEVICE to a comma-separated list, e.g. `tcp://127.0.0.1:47800`.

mod link;

use std::collections::HashMap;
use std::io::Cursor;
use std::sync::mpsc::{Receiver, Sender};
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

use base64::Engine;
use image::imageops::FilterType;
use link::{Event, Link};
use openaction::global_events::{GlobalEventHandler, SetBrightnessEvent, SetImageEvent, SystemDidWakeUpEvent};
use openaction::{OpenActionResult, async_trait, device_plugin};

/// Stream Deck SDK device type 7 = "Stream Deck +": keys, dials and a touch strip.
const DEVICE_TYPE: u8 = 7;
const NAMESPACE: &str = "td";

enum Job {
	Image(SetImageEvent),
	Brightness(u8),
}

struct OpenDeck {
	url: String,
	jobs: Sender<Job>,
}

/// Open decks by OpenDeck device id.
static DECKS: LazyLock<Mutex<HashMap<String, OpenDeck>>> = LazyLock::new(|| Mutex::new(HashMap::new()));

fn decks() -> std::sync::MutexGuard<'static, HashMap<String, OpenDeck>> {
	DECKS.lock().unwrap_or_else(|e| e.into_inner())
}

fn decode_data_url(url: &str) -> Result<image::RgbImage, String> {
	let data = url.split_once(',').map(|(_, d)| d).ok_or("not a data URL")?;
	let bytes = base64::engine::general_purpose::STANDARD.decode(data).map_err(|e| e.to_string())?;
	let img = image::load_from_memory(&bytes).map_err(|e| e.to_string())?;
	// Flatten transparency onto black, like the key's unlit state.
	let rgba = img.to_rgba8();
	let mut rgb = image::RgbImage::new(rgba.width(), rgba.height());
	for (dst, src) in rgb.pixels_mut().zip(rgba.pixels()) {
		let a = src[3] as u16;
		*dst = image::Rgb([(src[0] as u16 * a / 255) as u8, (src[1] as u16 * a / 255) as u8, (src[2] as u16 * a / 255) as u8]);
	}
	Ok(rgb)
}

fn to_jpeg(img: image::RgbImage, w: u32, h: u32) -> Result<Vec<u8>, String> {
	let img = if img.dimensions() == (w, h) { img } else { image::imageops::resize(&img, w, h, FilterType::Lanczos3) };
	let mut out = Vec::new();
	image::codecs::jpeg::JpegEncoder::new_with_quality(Cursor::new(&mut out), 92)
		.encode_image(&img)
		.map_err(|e| e.to_string())?;
	Ok(out)
}

fn apply_image(link: &Link, ev: &SetImageEvent) -> Result<(), String> {
	let info = &link.info;
	let io = |r: std::io::Result<()>| r.map_err(|e| e.to_string());
	let Some(position) = ev.position else {
		return io(link.clear_all());
	};
	let encoder = ev.controller.as_deref() == Some("Encoder");
	if encoder {
		let (w, h) = (info.segment_width(), info.strip_h);
		if position >= info.strip_segments || w == 0 {
			return Ok(());
		}
		let img = match &ev.image {
			Some(url) => decode_data_url(url)?,
			None => image::RgbImage::new(w as u32, h as u32),
		};
		io(link.set_strip_jpeg(position as u16 * w, w, h, &to_jpeg(img, w as u32, h as u32)?))
	} else {
		if position >= info.rows * info.cols {
			return Ok(());
		}
		match &ev.image {
			Some(url) => io(link.set_key_jpeg(position, &to_jpeg(decode_data_url(url)?, info.key_w as u32, info.key_h as u32)?)),
			None => io(link.clear_key(position)),
		}
	}
}

/// Applies display jobs in order. When updates pile up, only the newest image
/// per key or segment is sent.
fn display_worker(link: Link, jobs: Receiver<Job>) {
	while let Ok(first) = jobs.recv() {
		let mut batch = vec![first];
		batch.extend(jobs.try_iter());
		let mut keep = vec![true; batch.len()];
		for i in 0..batch.len() {
			let Job::Image(a) = &batch[i] else { continue };
			let superseded = batch[i + 1..].iter().any(|later| match later {
				Job::Image(b) => b.position.is_none() || (b.position == a.position && b.controller == a.controller),
				Job::Brightness(_) => false,
			});
			keep[i] = !superseded;
		}
		for (job, keep) in batch.iter().zip(keep) {
			if !keep {
				continue;
			}
			let result = match job {
				Job::Image(ev) => apply_image(&link, ev),
				Job::Brightness(pct) => link.set_brightness(*pct).map_err(|e| e.to_string()),
			};
			if let Err(e) = result {
				log::warn!("display update failed: {e}");
			}
		}
	}
}

async fn forward(id: &str, info: &link::Info, ev: Event) -> OpenActionResult<()> {
	let id = id.to_owned();
	match ev {
		Event::Key { key, pressed: true } => device_plugin::key_down(id, key).await,
		Event::Key { key, pressed: false } => device_plugin::key_up(id, key).await,
		Event::EncoderRotate { encoder, delta } => device_plugin::encoder_change(id, encoder, delta as i16).await,
		Event::EncoderButton { encoder, pressed: true } => device_plugin::encoder_down(id, encoder).await,
		Event::EncoderButton { encoder, pressed: false } => device_plugin::encoder_up(id, encoder).await,
		Event::Touch { kind, x, y } if kind <= 1 && info.segment_width() > 0 => {
			// OpenDeck works in Stream Deck + units: 200 x 100 per dial segment.
			let sw = info.segment_width() as u32;
			let segment = (x as u32 / sw).min(info.strip_segments as u32 - 1);
			let local_x = (x as u32 - segment * sw) * 200 / sw;
			let local_y = y as u32 * 100 / info.strip_h.max(1) as u32;
			device_plugin::touchscreen_press(id, segment as u8, local_x as u16, local_y as u16, kind == 1).await
		}
		// Drags have no OpenDeck event; power changes need none.
		Event::Touch { .. } | Event::Power { .. } | Event::Closed => Ok(()),
	}
}

async fn attach(url: String) {
	let opened = {
		let url = url.clone();
		tokio::task::spawn_blocking(move || Link::open(&url)).await
	};
	let (link, events) = match opened {
		Ok(Ok(v)) => v,
		Ok(Err(e)) => {
			log::debug!("{url}: {e}");
			return;
		}
		Err(_) => return,
	};
	let info = link.info.clone();
	let id = format!("{NAMESPACE}-{}", if info.serial.is_empty() { "unknown" } else { &info.serial });
	log::info!("{url}: Tessera Deck {} ({}x{} keys, {} dials)", info.serial, info.rows, info.cols, info.encoders);

	let (job_tx, job_rx) = std::sync::mpsc::channel();
	if decks().insert(id.clone(), OpenDeck { url: url.clone(), jobs: job_tx }).is_some() {
		log::warn!("two decks report serial {}; only the newest is used", info.serial);
	}
	std::thread::spawn(move || display_worker(link, job_rx));

	if let Err(e) = device_plugin::register_device(id.clone(), "Tessera Deck".into(), info.rows, info.cols, info.encoders, DEVICE_TYPE).await {
		log::error!("registerDevice failed: {e}");
	}

	// Bridge the blocking event channel into the async runtime.
	let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
	std::thread::spawn(move || {
		for ev in events {
			let closed = ev == Event::Closed;
			if tx.send(ev).is_err() || closed {
				break;
			}
		}
	});
	while let Some(ev) = rx.recv().await {
		if ev == Event::Closed {
			break;
		}
		if let Err(e) = forward(&id, &info, ev).await {
			log::warn!("could not forward input: {e}");
		}
	}

	log::info!("{url}: disconnected");
	decks().remove(&id);
	let _ = device_plugin::unregister_device(id).await;
}

/// Looks for decks every two seconds and attaches the new ones.
async fn watcher() {
	let forced: Vec<String> = std::env::var("TESSERA_DEVICE")
		.map(|v| v.split(',').map(str::trim).filter(|s| !s.is_empty()).map(String::from).collect())
		.unwrap_or_default();
	let mut attaching: Vec<(String, tokio::task::JoinHandle<()>)> = Vec::new();
	loop {
		let mut candidates = forced.clone();
		candidates.extend(tokio::task::spawn_blocking(link::find_ports).await.unwrap_or_default());
		attaching.retain(|(_, task)| !task.is_finished());
		for url in candidates {
			if !attaching.iter().any(|(u, _)| *u == url) {
				attaching.push((url.clone(), tokio::spawn(attach(url))));
			}
		}
		tokio::time::sleep(Duration::from_secs(2)).await;
	}
}

struct Handler;

#[async_trait]
impl GlobalEventHandler for Handler {
	async fn plugin_ready(&self) -> OpenActionResult<()> {
		tokio::spawn(watcher());
		Ok(())
	}

	async fn device_plugin_set_image(&self, event: SetImageEvent) -> OpenActionResult<()> {
		if let Some(deck) = decks().get(&event.device) {
			let _ = deck.jobs.send(Job::Image(event));
		}
		Ok(())
	}

	async fn device_plugin_set_brightness(&self, event: SetBrightnessEvent) -> OpenActionResult<()> {
		if let Some(deck) = decks().get(&event.device) {
			let _ = deck.jobs.send(Job::Brightness(event.brightness));
		}
		Ok(())
	}

	async fn system_did_wake_up(&self, _event: SystemDidWakeUpEvent) -> OpenActionResult<()> {
		let ids: Vec<String> = decks().keys().cloned().collect();
		for id in ids {
			let _ = device_plugin::rerender_images(id).await;
		}
		Ok(())
	}
}

#[tokio::main]
async fn main() -> OpenActionResult<()> {
	let _ = simplelog::TermLogger::init(
		simplelog::LevelFilter::Info,
		simplelog::Config::default(),
		simplelog::TerminalMode::Stdout,
		simplelog::ColorChoice::Never,
	);
	openaction::global_events::set_global_event_handler(&Handler);
	let result = openaction::run(std::env::args().collect()).await;
	for deck in decks().values() {
		log::info!("closing {}", deck.url);
	}
	result
}
