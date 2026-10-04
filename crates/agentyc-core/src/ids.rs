//! Validated logical identities and monotonic protocol values.

use std::{fmt, str::FromStr};

use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use thiserror::Error;

/// Maximum encoded length of a logical identifier.
pub const MAX_ID_LENGTH: usize = 128;

/// Error returned when a logical identifier is malformed.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum IdError {
    /// The value does not use the required prefix.
    #[error("id must use prefix {expected:?}")]
    InvalidPrefix {
        /// Required prefix.
        expected: &'static str,
    },
    /// The value has no suffix after its prefix.
    #[error("id suffix must not be empty")]
    EmptySuffix,
    /// The value is longer than the wire contract permits.
    #[error("id exceeds the {max} byte limit")]
    TooLong {
        /// Maximum encoded length.
        max: usize,
    },
    /// The value contains a character outside the restricted identifier alphabet.
    #[error("id contains an invalid character")]
    InvalidCharacter,
    /// A numeric value was not valid for the requested type.
    #[error("numeric value is out of range")]
    NumericRange,
    /// A content hash uses an unsupported or malformed representation.
    #[error("invalid content hash")]
    InvalidHash,
}

fn validate_identifier(value: &str, prefix: &'static str) -> Result<(), IdError> {
    if value.len() > MAX_ID_LENGTH {
        return Err(IdError::TooLong { max: MAX_ID_LENGTH });
    }
    if !value.starts_with(prefix) {
        return Err(IdError::InvalidPrefix { expected: prefix });
    }
    let suffix = &value[prefix.len()..];
    if suffix.is_empty() {
        return Err(IdError::EmptySuffix);
    }
    let mut bytes = suffix.bytes();
    let Some(first) = bytes.next() else {
        return Err(IdError::EmptySuffix);
    };
    if !(first.is_ascii_lowercase() || first.is_ascii_digit())
        || !bytes.all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_' || byte == b'-'
        })
    {
        return Err(IdError::InvalidCharacter);
    }
    Ok(())
}

macro_rules! opaque_id {
    ($(#[$meta:meta])* $name:ident, $prefix:literal) => {
        $(#[$meta])*
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
        #[must_use]
        pub struct $name(String);

        impl $name {
            /// Prefix used by this logical identity on the wire.
            pub const PREFIX: &'static str = $prefix;

            /// Validate and construct an identity from its complete wire value.
            pub fn new(value: impl Into<String>) -> Result<Self, IdError> {
                let value = value.into();
                validate_identifier(&value, Self::PREFIX)?;
                Ok(Self(value))
            }

            /// Construct an identity from a validated suffix.
            pub fn from_suffix(suffix: impl AsRef<str>) -> Result<Self, IdError> {
                let mut value = String::with_capacity(Self::PREFIX.len() + suffix.as_ref().len());
                value.push_str(Self::PREFIX);
                value.push_str(suffix.as_ref());
                Self::new(value)
            }

            /// Borrow the complete wire value.
            pub fn as_str(&self) -> &str {
                &self.0
            }

            /// Consume the identity and return its wire value.
            pub fn into_string(self) -> String {
                self.0
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                self.as_str()
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(self.as_str())
            }
        }

        impl FromStr for $name {
            type Err = IdError;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                Self::new(value)
            }
        }

        impl TryFrom<String> for $name {
            type Error = IdError;

            fn try_from(value: String) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl TryFrom<&str> for $name {
            type Error = IdError;

            fn try_from(value: &str) -> Result<Self, Self::Error> {
                Self::new(value)
            }
        }

        impl Serialize for $name {
            fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
            where
                S: Serializer,
            {
                serializer.serialize_str(self.as_str())
            }
        }

        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
            where
                D: Deserializer<'de>,
            {
                struct IdentifierVisitor;

                impl<'de> de::Visitor<'de> for IdentifierVisitor {
                    type Value = $name;

                    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                        formatter.write_str(concat!("a validated ", $prefix, " identity"))
                    }

                    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
                    where
                        E: de::Error,
                    {
                        $name::new(value).map_err(E::custom)
                    }

                    fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
                    where
                        E: de::Error,
                    {
                        $name::new(value).map_err(E::custom)
                    }
                }

                deserializer.deserialize_string(IdentifierVisitor)
            }
        }
    };
}

opaque_id!(PrincipalId, "principal_");
opaque_id!(ClientId, "client_");
opaque_id!(ConnectionNonce, "nonce_");
opaque_id!(ProfileBindingId, "profile_");
opaque_id!(SpaceId, "space_");
opaque_id!(PageId, "page_");
opaque_id!(DocumentId, "document_");
opaque_id!(NavigationId, "navigation_");
opaque_id!(SnapshotId, "snapshot_");
opaque_id!(FrameId, "frame_");
opaque_id!(RequestId, "req_");
opaque_id!(ActionId, "action_");
opaque_id!(UserIntentTicketId, "ticket_");
opaque_id!(EventId, "evt_");
opaque_id!(RefId, "ref_");
opaque_id!(IdempotencyKey, "idem_");
opaque_id!(ReconcileToken, "reconcile_");
opaque_id!(ArtifactId, "artifact_");
opaque_id!(ElementKey, "element_");

/// Compatibility alias for the host-issued user-intent ticket identity.
///
/// New code should prefer [`UserIntentTicketId`], while the alias keeps the
/// shorter name available to adapters that already call these values tickets.
pub type TicketId = UserIntentTicketId;

macro_rules! counter_id {
    ($(#[$meta:meta])* $name:ident) => {
        $(#[$meta])*
        #[derive(Debug, Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(u64);

        impl $name {
            /// Construct a value from its wire representation.
            pub const fn new(value: u64) -> Self {
                Self(value)
            }

            /// Return the wire representation.
            pub const fn get(self) -> u64 {
                self.0
            }

            /// Return the next value, or `None` on overflow.
            pub const fn checked_next(self) -> Option<Self> {
                match self.0.checked_add(1) {
                    Some(value) => Some(Self(value)),
                    None => None,
                }
            }
        }
    };
}

counter_id!(Generation);
counter_id!(LeaseEpoch);
counter_id!(SnapshotVersion);
counter_id!(DeltaSequence);
counter_id!(EventSequence);
counter_id!(TopologyVersion);
counter_id!(RefEpoch);
counter_id!(Timestamp);
counter_id!(BrokerEpoch);
counter_id!(ConnectionEpoch);
counter_id!(BrowserSessionEpoch);
counter_id!(WorkerInstanceEpoch);
counter_id!(FrameVersion);

/// Content fingerprint used by snapshot and request contracts.
///
/// The core uses FNV-1a-64 as a deterministic, non-authenticating fingerprint.
/// It is deliberately not presented as a cryptographic signature.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[must_use]
pub struct ContentHash(String);

impl ContentHash {
    /// Name of the deterministic fingerprint algorithm.
    pub const ALGORITHM: &'static str = "fnv1a64";

    /// Construct a hash after validating its wire representation.
    pub fn new(value: impl Into<String>) -> Result<Self, IdError> {
        let value = value.into();
        let prefix = "fnv1a64:";
        let valid = value.len() == prefix.len() + 16
            && value.starts_with(prefix)
            && value[prefix.len()..]
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit());
        if valid {
            Ok(Self(value))
        } else {
            Err(IdError::InvalidHash)
        }
    }

    /// Compute a deterministic fingerprint for bytes.
    pub fn from_bytes(bytes: &[u8]) -> Self {
        let mut hash = 0xcbf29ce484222325_u64;
        for byte in bytes {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x100000001b3);
        }
        Self(format!("{}:{hash:016x}", Self::ALGORITHM))
    }

    /// Borrow the complete wire value.
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Consume the hash and return its wire value.
    pub fn into_string(self) -> String {
        self.0
    }
}

impl AsRef<str> for ContentHash {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}

impl fmt::Display for ContentHash {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for ContentHash {
    type Err = IdError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::new(value)
    }
}

impl Serialize for ContentHash {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for ContentHash {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::new(value).map_err(de::Error::custom)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn logical_ids_require_their_own_prefix() {
        assert!(SpaceId::new("space_alpha-1").is_ok());
        assert!(SpaceId::new("page_alpha-1").is_err());
        assert!(SpaceId::new("space_").is_err());
        assert!(SpaceId::new("space_Alpha").is_err());
    }

    #[test]
    fn document_navigation_and_snapshot_ids_are_validated_and_round_trip() {
        let cases = [
            (DocumentId::PREFIX, "document_doc-1"),
            (NavigationId::PREFIX, "navigation_nav-1"),
            (SnapshotId::PREFIX, "snapshot_snap-1"),
        ];

        for (prefix, value) in cases {
            assert!(validate_identifier(value, prefix).is_ok());
            assert!(validate_identifier(&format!("{prefix}raw.target"), prefix).is_err());
            assert!(validate_identifier(prefix, prefix).is_err());
            let max_suffix_length = MAX_ID_LENGTH - prefix.len();
            assert!(
                validate_identifier(
                    &format!("{prefix}{}", "a".repeat(max_suffix_length)),
                    prefix
                )
                .is_ok()
            );
            assert!(
                validate_identifier(
                    &format!("{prefix}{}", "a".repeat(max_suffix_length + 1)),
                    prefix
                )
                .is_err()
            );
        }

        let document = DocumentId::from_suffix("doc-1").expect("document");
        let navigation = NavigationId::from_suffix("nav-1").expect("navigation");
        let snapshot = SnapshotId::from_suffix("snap-1").expect("snapshot");
        assert!(DocumentId::new("target_123").is_err());
        assert!(DocumentId::new("document_é").is_err());
        assert!(NavigationId::new("navigation_").is_err());
        assert!(SnapshotId::new("document_snap-1").is_err());

        assert_eq!(
            serde_json::to_string(&document).expect("serialize"),
            r#""document_doc-1""#
        );
        assert_eq!(
            serde_json::from_str::<DocumentId>(r#""document_doc-1""#).expect("deserialize"),
            document
        );
        assert_eq!(
            serde_json::to_string(&navigation).expect("serialize"),
            r#""navigation_nav-1""#
        );
        assert_eq!(
            serde_json::from_str::<NavigationId>(r#""navigation_nav-1""#).expect("deserialize"),
            navigation
        );
        assert_eq!(
            serde_json::to_string(&snapshot).expect("serialize"),
            r#""snapshot_snap-1""#
        );
        assert_eq!(
            serde_json::from_str::<SnapshotId>(r#""snapshot_snap-1""#).expect("deserialize"),
            snapshot
        );

        assert!(serde_json::from_str::<DocumentId>(r#""target_123""#).is_err());
        assert!(serde_json::from_str::<NavigationId>(r#""session_123""#).is_err());
        assert!(serde_json::from_str::<SnapshotId>(r#""snapshot_raw.target""#).is_err());
    }

    #[test]
    fn content_hashes_are_stable_and_validated() {
        let first = ContentHash::from_bytes(b"same");
        let second = ContentHash::from_bytes(b"same");
        assert_eq!(first, second);
        assert!(ContentHash::new(first.as_str()).is_ok());
        assert!(ContentHash::new("sha256:bad").is_err());
    }
}
