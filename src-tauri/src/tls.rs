//! Self-signed certificate handling.
//!
//! No certificate authority will issue a certificate for a private LAN
//! IP, so this generates its own once and reuses it forever. Clients pin
//! the fingerprint on first connect and require it to match afterwards —
//! the same trust model SSH uses for host keys.
//!
//! Deleting cert.pem/key.pem regenerates a *different* certificate, and
//! every device that already pinned the old one will refuse to connect
//! until re-paired. That is the point, not a bug.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

pub struct Identity {
    pub cert_pem: PathBuf,
    pub key_pem: PathBuf,
    pub fingerprint: String,
}

pub fn ensure_identity(dir: &Path) -> Result<Identity> {
    let cert_pem = dir.join("cert.pem");
    let key_pem = dir.join("key.pem");

    if !cert_pem.exists() || !key_pem.exists() {
        generate(&cert_pem, &key_pem).context("generating a TLS certificate")?;
    }

    let fingerprint = fingerprint_of(&cert_pem)?;
    Ok(Identity { cert_pem, key_pem, fingerprint })
}

fn generate(cert_path: &Path, key_path: &Path) -> Result<()> {
    // The names here are cosmetic: clients verify the pinned fingerprint,
    // not the subject, because the address changes with the network.
    let names = vec!["PC Bridge".to_string(), "localhost".to_string()];
    let certified = rcgen::generate_simple_self_signed(names)?;

    if let Some(parent) = cert_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(cert_path, certified.cert.pem())?;
    std::fs::write(key_path, certified.signing_key.serialize_pem())?;
    Ok(())
}

/// SHA-256 over the certificate's DER bytes, formatted `AA:BB:CC:…`.
/// This is the string a person compares by eye when pairing, so the
/// formatting matters as much as the hash.
pub fn fingerprint_of(cert_path: &Path) -> Result<String> {
    let pem = std::fs::read_to_string(cert_path)?;
    let der = pem_to_der(&pem).context("certificate file is not valid PEM")?;
    Ok(format_fingerprint(&Sha256::digest(&der)))
}

pub fn format_fingerprint(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|b| format!("{b:02X}"))
        .collect::<Vec<_>>()
        .join(":")
}

fn pem_to_der(pem: &str) -> Option<Vec<u8>> {
    let body: String = pem
        .lines()
        .skip_while(|l| !l.starts_with("-----BEGIN"))
        .skip(1)
        .take_while(|l| !l.starts_with("-----END"))
        .collect();
    base64_decode(&body)
}

/// A tiny base64 decoder, so PEM parsing doesn't pull in another crate
/// for the one place the whole project needs it.
fn base64_decode(input: &str) -> Option<Vec<u8>> {
    const TABLE: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    let mut out = Vec::with_capacity(input.len() / 4 * 3);
    let mut buffer: u32 = 0;
    let mut bits: u32 = 0;

    for ch in input.bytes() {
        if ch == b'=' || ch.is_ascii_whitespace() {
            continue;
        }
        let value = TABLE.iter().position(|&t| t == ch)? as u32;
        buffer = (buffer << 6) | value;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
        }
    }
    Some(out)
}
