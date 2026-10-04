//! Action requests and receipts, including unknown and reconciliation states.

use std::{collections::BTreeMap, fmt};

use serde::{Deserialize, Serialize, ser};
use thiserror::Error;

use crate::{
    errors::{CoreError, ErrorCode},
    ids::{
        ActionId, ConnectionEpoch, ConnectionNonce, ContentHash, Generation, IdempotencyKey,
        LeaseEpoch, PageId, ProfileBindingId, ReconcileToken, RequestId, SpaceId, Timestamp,
    },
    records::{Lease, UserIntentContext, UserIntentTicket},
    states::{ActionStatus, CompletionSource, DispatchState, NextAction, ReconciliationState},
};

/// Side-effect class understood by the host scheduler without naming a browser API.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionOperation {
    /// Navigate a logical page.
    Navigate,
    /// Activate a logical element ref.
    Click,
    /// Insert bounded text into a logical control.
    Input,
    /// Run a policy-approved evaluation operation.
    Evaluate,
    /// Scroll a logical page.
    Scroll,
    /// Wait for a condition or event.
    Wait,
    /// Capture a bounded artifact.
    Screenshot,
    /// Write storage under an explicit policy.
    StorageWrite,
    /// Write cookies under an explicit policy.
    CookieWrite,
    /// Upload data under an explicit policy.
    Upload,
    /// Close a logically owned page.
    Close,
}

impl ActionOperation {
    /// Return the canonical wire spelling used in action hashes.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Navigate => "navigate",
            Self::Click => "click",
            Self::Input => "input",
            Self::Evaluate => "evaluate",
            Self::Scroll => "scroll",
            Self::Wait => "wait",
            Self::Screenshot => "screenshot",
            Self::StorageWrite => "storage_write",
            Self::CookieWrite => "cookie_write",
            Self::Upload => "upload",
            Self::Close => "close",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CanonicalSerializeError(String);

impl fmt::Display for CanonicalSerializeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for CanonicalSerializeError {}

impl ser::Error for CanonicalSerializeError {
    fn custom<T: fmt::Display>(message: T) -> Self {
        Self(message.to_string())
    }
}

#[derive(Debug, Clone)]
enum CanonicalValue {
    Null,
    Bool(bool),
    Number(String),
    String(String),
    Array(Vec<Self>),
    Object(Vec<(String, Self)>),
}

impl CanonicalValue {
    fn object(mut fields: Vec<(String, Self)>) -> Result<Self, CanonicalSerializeError> {
        fields.sort_by(|left, right| left.0.cmp(&right.0));
        if fields.windows(2).any(|pair| pair[0].0 == pair[1].0) {
            return Err(CanonicalSerializeError(
                "canonical object contains duplicate keys".to_owned(),
            ));
        }
        Ok(Self::Object(fields))
    }

    fn render(&self, output: &mut String) {
        match self {
            Self::Null => output.push_str("null"),
            Self::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
            Self::Number(value) => output.push_str(value),
            Self::String(value) => append_json_string(output, value),
            Self::Array(values) => {
                output.push('[');
                for (index, value) in values.iter().enumerate() {
                    if index != 0 {
                        output.push(',');
                    }
                    value.render(output);
                }
                output.push(']');
            }
            Self::Object(fields) => {
                output.push('{');
                for (index, (key, value)) in fields.iter().enumerate() {
                    if index != 0 {
                        output.push(',');
                    }
                    append_json_string(output, key);
                    output.push(':');
                    value.render(output);
                }
                output.push('}');
            }
        }
    }
}

struct CanonicalValueSerializer;

fn append_json_string(output: &mut String, value: &str) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character.is_control() => {
                use std::fmt::Write;
                let _ = write!(output, "\\u{:04x}", character as u32);
            }
            character => output.push(character),
        }
    }
    output.push('"');
}

struct CanonicalSeq {
    values: Vec<CanonicalValue>,
}

struct CanonicalMap {
    fields: Vec<(String, CanonicalValue)>,
    pending_key: Option<String>,
}

struct CanonicalVariantSeq {
    variant: String,
    values: Vec<CanonicalValue>,
}

struct CanonicalVariantMap {
    variant: String,
    fields: Vec<(String, CanonicalValue)>,
}

impl ser::Serializer for &mut CanonicalValueSerializer {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;
    type SerializeSeq = CanonicalSeq;
    type SerializeTuple = CanonicalSeq;
    type SerializeTupleStruct = CanonicalSeq;
    type SerializeTupleVariant = CanonicalVariantSeq;
    type SerializeMap = CanonicalMap;
    type SerializeStruct = CanonicalMap;
    type SerializeStructVariant = CanonicalVariantMap;

    fn serialize_bool(self, value: bool) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Bool(value))
    }

    fn serialize_i8(self, value: i8) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_i16(self, value: i16) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_i32(self, value: i32) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_i64(self, value: i64) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_i128(self, value: i128) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_u8(self, value: u8) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_u16(self, value: u16) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_u32(self, value: u32) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_u64(self, value: u64) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_u128(self, value: u128) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_f32(self, value: f32) -> Result<Self::Ok, Self::Error> {
        self.serialize_f64(f64::from(value))
    }

    fn serialize_f64(self, value: f64) -> Result<Self::Ok, Self::Error> {
        if !value.is_finite() {
            return Err(CanonicalSerializeError(
                "canonical action payload cannot contain a non-finite number".to_owned(),
            ));
        }
        Ok(CanonicalValue::Number(value.to_string()))
    }

    fn serialize_char(self, value: char) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::String(value.to_string()))
    }

    fn serialize_str(self, value: &str) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::String(value.to_owned()))
    }

    fn serialize_bytes(self, value: &[u8]) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Array(
            value
                .iter()
                .map(|byte| CanonicalValue::Number(byte.to_string()))
                .collect(),
        ))
    }

    fn serialize_none(self) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Null)
    }

    fn serialize_some<T>(self, value: &T) -> Result<Self::Ok, Self::Error>
    where
        T: ?Sized + Serialize,
    {
        value.serialize(self)
    }

    fn serialize_unit(self) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Null)
    }

    fn serialize_unit_struct(self, _name: &'static str) -> Result<Self::Ok, Self::Error> {
        self.serialize_unit()
    }

    fn serialize_unit_variant(
        self,
        _name: &'static str,
        _variant_index: u32,
        variant: &'static str,
    ) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::String(variant.to_owned()))
    }

    fn serialize_newtype_struct<T>(
        self,
        _name: &'static str,
        value: &T,
    ) -> Result<Self::Ok, Self::Error>
    where
        T: ?Sized + Serialize,
    {
        value.serialize(self)
    }

    fn serialize_newtype_variant<T>(
        self,
        _name: &'static str,
        _variant_index: u32,
        variant: &'static str,
        value: &T,
    ) -> Result<Self::Ok, Self::Error>
    where
        T: ?Sized + Serialize,
    {
        CanonicalValue::object(vec![(
            variant.to_owned(),
            value.serialize(&mut CanonicalValueSerializer)?,
        )])
    }

    fn serialize_seq(self, _len: Option<usize>) -> Result<Self::SerializeSeq, Self::Error> {
        Ok(CanonicalSeq { values: Vec::new() })
    }

    fn serialize_tuple(self, _len: usize) -> Result<Self::SerializeTuple, Self::Error> {
        self.serialize_seq(None)
    }

    fn serialize_tuple_struct(
        self,
        _name: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeTupleStruct, Self::Error> {
        self.serialize_seq(None)
    }

    fn serialize_tuple_variant(
        self,
        _name: &'static str,
        _variant_index: u32,
        variant: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeTupleVariant, Self::Error> {
        Ok(CanonicalVariantSeq {
            variant: variant.to_owned(),
            values: Vec::new(),
        })
    }

    fn serialize_map(self, _len: Option<usize>) -> Result<Self::SerializeMap, Self::Error> {
        Ok(CanonicalMap {
            fields: Vec::new(),
            pending_key: None,
        })
    }

    fn serialize_struct(
        self,
        _name: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeStruct, Self::Error> {
        Ok(CanonicalMap {
            fields: Vec::new(),
            pending_key: None,
        })
    }

    fn serialize_struct_variant(
        self,
        _name: &'static str,
        _variant_index: u32,
        variant: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeStructVariant, Self::Error> {
        Ok(CanonicalVariantMap {
            variant: variant.to_owned(),
            fields: Vec::new(),
        })
    }

    fn is_human_readable(&self) -> bool {
        true
    }
}

impl ser::SerializeSeq for CanonicalSeq {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;

    fn serialize_element<T>(&mut self, value: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        self.values
            .push(value.serialize(&mut CanonicalValueSerializer)?);
        Ok(())
    }

    fn end(self) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Array(self.values))
    }
}

impl ser::SerializeTuple for CanonicalSeq {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;

    fn serialize_element<T>(&mut self, value: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        self.values
            .push(value.serialize(&mut CanonicalValueSerializer)?);
        Ok(())
    }

    fn end(self) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Array(self.values))
    }
}

impl ser::SerializeTupleStruct for CanonicalSeq {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;

    fn serialize_field<T>(&mut self, value: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        self.values
            .push(value.serialize(&mut CanonicalValueSerializer)?);
        Ok(())
    }

    fn end(self) -> Result<Self::Ok, Self::Error> {
        Ok(CanonicalValue::Array(self.values))
    }
}

impl ser::SerializeTupleVariant for CanonicalVariantSeq {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;

    fn serialize_field<T>(&mut self, value: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        self.values
            .push(value.serialize(&mut CanonicalValueSerializer)?);
        Ok(())
    }

    fn end(self) -> Result<Self::Ok, Self::Error> {
        CanonicalValue::object(vec![(self.variant, CanonicalValue::Array(self.values))])
    }
}

impl ser::SerializeMap for CanonicalMap {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;

    fn serialize_key<T>(&mut self, key: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        self.pending_key = Some(key.serialize(&mut CanonicalKeySerializer)?);
        Ok(())
    }

    fn serialize_value<T>(&mut self, value: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        let key = self.pending_key.take().ok_or_else(|| {
            CanonicalSerializeError("canonical map value was serialized before its key".to_owned())
        })?;
        self.fields
            .push((key, value.serialize(&mut CanonicalValueSerializer)?));
        Ok(())
    }

    fn end(self) -> Result<Self::Ok, Self::Error> {
        if self.pending_key.is_some() {
            return Err(CanonicalSerializeError(
                "canonical map ended with a key without a value".to_owned(),
            ));
        }
        CanonicalValue::object(self.fields)
    }
}

impl ser::SerializeStruct for CanonicalMap {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;

    fn serialize_field<T>(&mut self, key: &'static str, value: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        self.fields.push((
            key.to_owned(),
            value.serialize(&mut CanonicalValueSerializer)?,
        ));
        Ok(())
    }

    fn end(self) -> Result<Self::Ok, Self::Error> {
        CanonicalValue::object(self.fields)
    }
}

impl ser::SerializeStructVariant for CanonicalVariantMap {
    type Ok = CanonicalValue;
    type Error = CanonicalSerializeError;

    fn serialize_field<T>(&mut self, key: &'static str, value: &T) -> Result<(), Self::Error>
    where
        T: ?Sized + Serialize,
    {
        self.fields.push((
            key.to_owned(),
            value.serialize(&mut CanonicalValueSerializer)?,
        ));
        Ok(())
    }

    fn end(self) -> Result<Self::Ok, Self::Error> {
        CanonicalValue::object(vec![(self.variant, CanonicalValue::object(self.fields)?)])
    }
}

struct CanonicalKeySerializer;

impl ser::Serializer for &mut CanonicalKeySerializer {
    type Ok = String;
    type Error = CanonicalSerializeError;
    type SerializeSeq = ser::Impossible<String, CanonicalSerializeError>;
    type SerializeTuple = ser::Impossible<String, CanonicalSerializeError>;
    type SerializeTupleStruct = ser::Impossible<String, CanonicalSerializeError>;
    type SerializeTupleVariant = ser::Impossible<String, CanonicalSerializeError>;
    type SerializeMap = ser::Impossible<String, CanonicalSerializeError>;
    type SerializeStruct = ser::Impossible<String, CanonicalSerializeError>;
    type SerializeStructVariant = ser::Impossible<String, CanonicalSerializeError>;

    fn serialize_bool(self, value: bool) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_i8(self, value: i8) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_i16(self, value: i16) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_i32(self, value: i32) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_i64(self, value: i64) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_i128(self, value: i128) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_u8(self, value: u8) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_u16(self, value: u16) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_u32(self, value: u32) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_u64(self, value: u64) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_u128(self, value: u128) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_f32(self, value: f32) -> Result<Self::Ok, Self::Error> {
        if value.is_finite() {
            Ok(value.to_string())
        } else {
            Err(CanonicalSerializeError("non-finite map key".to_owned()))
        }
    }
    fn serialize_f64(self, value: f64) -> Result<Self::Ok, Self::Error> {
        if value.is_finite() {
            Ok(value.to_string())
        } else {
            Err(CanonicalSerializeError("non-finite map key".to_owned()))
        }
    }
    fn serialize_char(self, value: char) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_string())
    }
    fn serialize_str(self, value: &str) -> Result<Self::Ok, Self::Error> {
        Ok(value.to_owned())
    }
    fn serialize_bytes(self, _value: &[u8]) -> Result<Self::Ok, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_none(self) -> Result<Self::Ok, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys cannot be null".to_owned(),
        ))
    }
    fn serialize_some<T>(self, _value: &T) -> Result<Self::Ok, Self::Error>
    where
        T: ?Sized + Serialize,
    {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_unit(self) -> Result<Self::Ok, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys cannot be unit".to_owned(),
        ))
    }
    fn serialize_unit_struct(self, _name: &'static str) -> Result<Self::Ok, Self::Error> {
        self.serialize_unit()
    }
    fn serialize_unit_variant(
        self,
        _name: &'static str,
        _variant_index: u32,
        variant: &'static str,
    ) -> Result<Self::Ok, Self::Error> {
        Ok(variant.to_owned())
    }
    fn serialize_newtype_struct<T>(
        self,
        _name: &'static str,
        _value: &T,
    ) -> Result<Self::Ok, Self::Error>
    where
        T: ?Sized + Serialize,
    {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_newtype_variant<T>(
        self,
        _name: &'static str,
        _variant_index: u32,
        _variant: &'static str,
        _value: &T,
    ) -> Result<Self::Ok, Self::Error>
    where
        T: ?Sized + Serialize,
    {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_seq(self, _len: Option<usize>) -> Result<Self::SerializeSeq, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_tuple(self, _len: usize) -> Result<Self::SerializeTuple, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_tuple_struct(
        self,
        _name: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeTupleStruct, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_tuple_variant(
        self,
        _name: &'static str,
        _variant_index: u32,
        _variant: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeTupleVariant, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_map(self, _len: Option<usize>) -> Result<Self::SerializeMap, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_struct(
        self,
        _name: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeStruct, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
    fn serialize_struct_variant(
        self,
        _name: &'static str,
        _variant_index: u32,
        _variant: &'static str,
        _len: usize,
    ) -> Result<Self::SerializeStructVariant, Self::Error> {
        Err(CanonicalSerializeError(
            "map keys must be scalar values".to_owned(),
        ))
    }
}

/// Serialize the operation, payload, and logical scope into canonical bytes.
pub fn canonical_action_bytes<P: Serialize>(
    operation: ActionOperation,
    payload: &P,
    space_id: &SpaceId,
    page_id: Option<&PageId>,
) -> Result<Vec<u8>, CoreError> {
    let payload = payload
        .serialize(&mut CanonicalValueSerializer)
        .map_err(|error| {
            CoreError::invalid_argument(format!("action payload is not canonical: {error}"))
        })?;
    let value = CanonicalValue::object(vec![
        (
            "operation".to_owned(),
            CanonicalValue::String(operation.as_str().to_owned()),
        ),
        ("payload".to_owned(), payload),
        (
            "scope".to_owned(),
            CanonicalValue::object(vec![
                (
                    "page_id".to_owned(),
                    page_id.map_or(CanonicalValue::Null, |id| {
                        CanonicalValue::String(id.as_str().to_owned())
                    }),
                ),
                (
                    "space_id".to_owned(),
                    CanonicalValue::String(space_id.as_str().to_owned()),
                ),
            ])
            .map_err(|error| CoreError::invalid_argument(error.to_string()))?,
        ),
    ])
    .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
    let mut output = String::new();
    value.render(&mut output);
    Ok(output.into_bytes())
}

/// Compute the deterministic action hash used by confirmation and idempotency contracts.
pub fn canonical_action_hash<P: Serialize>(
    operation: ActionOperation,
    payload: &P,
    space_id: &SpaceId,
    page_id: Option<&PageId>,
) -> Result<ContentHash, CoreError> {
    Ok(ContentHash::from_bytes(&canonical_action_bytes(
        operation, payload, space_id, page_id,
    )?))
}

/// Why an action outcome became unknown.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UnknownReason {
    /// The execution response was lost after dispatch.
    LostResponse,
    /// The bridge disconnected after dispatch.
    BridgeLost,
    /// The host restarted after dispatch.
    HostRestarted,
    /// A deadline elapsed after dispatch.
    TimeoutAfterDispatch,
    /// The browser session changed before completion was observed.
    BrowserSessionChanged,
    /// A takeover fence interrupted an in-flight operation.
    FenceInterrupted,
}

/// Optional action postcondition used during reconciliation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum Postcondition {
    /// The page must reach a logical generation.
    PageGeneration {
        /// Expected document generation.
        document_generation: Generation,
    },
    /// The page must produce a snapshot with this hash.
    SnapshotHash {
        /// Expected snapshot hash.
        snapshot_hash: ContentHash,
    },
}

/// A mutation request with all identities needed for idempotency and fencing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActionRequest<P = BTreeMap<String, String>> {
    /// Request identity.
    pub request_id: RequestId,
    /// Durable action identity.
    pub action_id: ActionId,
    /// Caller-supplied idempotency identity.
    pub idempotency_key: IdempotencyKey,
    /// Hash of the canonical request payload.
    pub request_hash: ContentHash,
    /// Owning logical space.
    pub space_id: SpaceId,
    /// Optional logical page target.
    pub page_id: Option<PageId>,
    /// Lease epoch presented for admission.
    pub lease_epoch: LeaseEpoch,
    /// Transport-neutral operation class.
    pub operation: ActionOperation,
    /// Typed operation payload.
    pub payload: P,
    /// Optional postcondition used during reconciliation.
    pub postcondition: Option<Postcondition>,
}

impl<P> ActionRequest<P> {
    /// Reject a request whose lease epoch is not current.
    pub fn validate_lease(&self, current: LeaseEpoch) -> Result<(), CoreError> {
        if self.lease_epoch != current {
            return Err(CoreError::stale_lease(
                current.get(),
                self.lease_epoch.get(),
            ));
        }
        Ok(())
    }

    /// Validate this request against a durable lease at a specific core time.
    pub fn validate_lease_at(&self, lease: &Lease, now: Timestamp) -> Result<(), CoreError> {
        lease.validate_epoch_at(self.lease_epoch, now)
    }

    /// Validate the request's ordinary-mutation scope against the current space.
    pub fn validate_space_admission(
        &self,
        space: &crate::records::SpaceDescriptor,
        principal_id: &crate::ids::PrincipalId,
        now: Timestamp,
    ) -> Result<(), CoreError> {
        space.validate_mutation_admission(principal_id, self.lease_epoch, now)
    }

    /// Validate and consume a user-intent ticket when policy requires confirmation.
    ///
    /// `required` is a host policy decision; it must never be derived from a
    /// caller-controlled payload boolean. Existing actions that do not cross a
    /// confirmation boundary can continue to omit the ticket.
    #[allow(clippy::too_many_arguments)]
    pub fn validate_user_intent(
        &self,
        ticket: Option<&mut UserIntentTicket>,
        required: bool,
        profile_binding_id: &ProfileBindingId,
        connection_epoch: ConnectionEpoch,
        connection_nonce: &ConnectionNonce,
        document_generation: Option<Generation>,
        now: Timestamp,
    ) -> Result<(), CoreError> {
        let Some(ticket) = ticket else {
            return if required {
                Err(CoreError::new(
                    ErrorCode::PermissionDenied,
                    "user-intent ticket is required",
                ))
            } else {
                Ok(())
            };
        };

        ticket.validate_and_consume(
            &UserIntentContext {
                profile_binding_id,
                space_id: &self.space_id,
                page_id: self.page_id.as_ref(),
                document_generation,
                action_hash: &self.request_hash,
                lease_epoch: self.lease_epoch,
                connection_epoch,
                connection_nonce,
            },
            now,
        )
    }
}

impl<P: Serialize> ActionRequest<P> {
    /// Compute the canonical hash derived only from operation, payload, and logical scope.
    pub fn canonical_hash(&self) -> Result<ContentHash, CoreError> {
        canonical_action_hash(
            self.operation,
            &self.payload,
            &self.space_id,
            self.page_id.as_ref(),
        )
    }

    /// Reject a request whose supplied hash is not the canonical action hash.
    pub fn validate_canonical_hash(&self) -> Result<(), CoreError> {
        let expected = self.canonical_hash()?;
        if self.request_hash != expected {
            return Err(CoreError::invalid_argument(
                "request hash does not match operation, payload, and logical scope",
            ));
        }
        Ok(())
    }
}

/// Durable receipt for one action attempt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActionReceipt {
    /// Durable action identity.
    pub action_id: ActionId,
    /// Request identity used for response matching.
    pub request_id: RequestId,
    /// Idempotency key used for duplicate behavior.
    pub idempotency_key: IdempotencyKey,
    /// Hash of the admitted request.
    pub request_hash: ContentHash,
    /// Owning logical space.
    pub space_id: SpaceId,
    /// Optional logical page target.
    pub page_id: Option<PageId>,
    /// Lease epoch used for admission.
    pub lease_epoch: LeaseEpoch,
    /// Operation class.
    pub operation: ActionOperation,
    /// Durable outcome status.
    pub status: ActionStatus,
    /// Dispatch boundary status.
    pub dispatch_state: DispatchState,
    /// Whether a safe retry is currently possible.
    pub retryable: bool,
    /// Whether the receipt has or had an unknown outcome.
    pub unknown: bool,
    /// Cause of an unknown outcome.
    pub unknown_reason: Option<UnknownReason>,
    /// Reconciliation state.
    pub reconciliation_state: ReconciliationState,
    /// Source of the definitive completion.
    pub completion_source: CompletionSource,
    /// Optional declared postcondition.
    pub postcondition: Option<Postcondition>,
    /// Stable terminal error code, when any.
    pub error_code: Option<ErrorCode>,
    /// Token used to reconcile an unknown outcome.
    pub reconcile_token: Option<ReconcileToken>,
    /// Client guidance after this receipt.
    pub next_action: NextAction,
    /// Start timestamp in the core clock domain.
    pub started_at: Option<Timestamp>,
    /// Completion timestamp in the core clock domain.
    pub completed_at: Option<Timestamp>,
}

impl ActionReceipt {
    /// Construct a queued receipt for a newly admitted action.
    #[allow(clippy::too_many_arguments)]
    pub fn queued(
        action_id: ActionId,
        request_id: RequestId,
        idempotency_key: IdempotencyKey,
        request_hash: ContentHash,
        space_id: SpaceId,
        page_id: Option<PageId>,
        lease_epoch: LeaseEpoch,
        operation: ActionOperation,
        postcondition: Option<Postcondition>,
        started_at: Option<Timestamp>,
    ) -> Self {
        Self {
            action_id,
            request_id,
            idempotency_key,
            request_hash,
            space_id,
            page_id,
            lease_epoch,
            operation,
            status: ActionStatus::Queued,
            dispatch_state: DispatchState::NotDispatched,
            retryable: false,
            unknown: false,
            unknown_reason: None,
            reconciliation_state: ReconciliationState::NotRequired,
            completion_source: CompletionSource::None,
            postcondition,
            error_code: None,
            reconcile_token: None,
            next_action: NextAction::None,
            started_at,
            completed_at: None,
        }
    }

    /// Validate the state, dispatch, reconciliation, and timestamp invariants.
    pub fn validate_invariants(&self) -> Result<(), ActionReceiptValidationError> {
        let invalid = |message: &str| {
            Err(ActionReceiptValidationError::Invariant {
                message: message.to_owned(),
            })
        };

        if self
            .started_at
            .zip(self.completed_at)
            .is_some_and(|(started, completed)| completed < started)
        {
            return invalid("completed_at must not precede started_at");
        }

        match self.status {
            ActionStatus::Queued => {
                if !matches!(
                    self.dispatch_state,
                    DispatchState::NotDispatched | DispatchState::Queued
                ) || self.unknown
                    || self.unknown_reason.is_some()
                    || self.reconciliation_state != ReconciliationState::NotRequired
                    || self.completion_source != CompletionSource::None
                    || self.error_code.is_some()
                    || self.retryable
                    || self.reconcile_token.is_some()
                    || self.next_action != NextAction::None
                    || self.completed_at.is_some()
                {
                    return invalid("queued receipt has terminal or dispatched fields");
                }
            }
            ActionStatus::Running => {
                if !matches!(
                    self.dispatch_state,
                    DispatchState::Dispatched | DispatchState::Acknowledged
                ) || self.unknown
                    || self.unknown_reason.is_some()
                    || self.reconciliation_state != ReconciliationState::NotRequired
                    || self.completion_source != CompletionSource::None
                    || self.error_code.is_some()
                    || self.retryable
                    || self.reconcile_token.is_some()
                    || self.next_action != NextAction::None
                    || self.completed_at.is_some()
                {
                    return invalid("running receipt has an invalid dispatch or completion state");
                }
            }
            ActionStatus::Succeeded => {
                if !matches!(
                    self.dispatch_state,
                    DispatchState::Dispatched | DispatchState::Acknowledged
                ) || self.unknown
                    || self.unknown_reason.is_some()
                    || !matches!(
                        self.completion_source,
                        CompletionSource::Extension
                            | CompletionSource::Reconciliation
                            | CompletionSource::Ledger
                    )
                    || self.retryable
                    || self.error_code.is_some()
                    || self.next_action != NextAction::None
                {
                    return invalid("succeeded receipt has an invalid outcome state");
                }
                if self.reconciliation_state == ReconciliationState::ReconciledSucceeded {
                    if self.completion_source != CompletionSource::Reconciliation
                        || self.reconcile_token.is_none()
                    {
                        return invalid("reconciled success requires reconciliation provenance");
                    }
                } else if self.reconciliation_state != ReconciliationState::NotRequired
                    || self.reconcile_token.is_some()
                {
                    return invalid("succeeded receipt has an invalid reconciliation state");
                }
            }
            ActionStatus::Failed => {
                if !matches!(
                    self.dispatch_state,
                    DispatchState::Dispatched | DispatchState::Acknowledged
                ) || self.unknown
                    || self.unknown_reason.is_some()
                    || self.error_code.is_none()
                    || self.error_code == Some(ErrorCode::UnknownOutcome)
                    || !matches!(
                        self.completion_source,
                        CompletionSource::Extension
                            | CompletionSource::Reconciliation
                            | CompletionSource::Ledger
                    )
                    || self.next_action
                        != if self.retryable {
                            NextAction::Retry
                        } else {
                            NextAction::None
                        }
                {
                    return invalid("failed receipt has an invalid outcome state");
                }
                if matches!(
                    self.reconciliation_state,
                    ReconciliationState::ReconciledFailed
                        | ReconciliationState::RequiresConfirmation
                ) && self.completion_source != CompletionSource::Reconciliation
                {
                    return invalid("reconciled failure requires reconciliation provenance");
                }
                if !matches!(
                    self.reconciliation_state,
                    ReconciliationState::NotRequired
                        | ReconciliationState::ReconciledFailed
                        | ReconciliationState::RequiresConfirmation
                ) || (self.reconciliation_state == ReconciliationState::NotRequired
                    && self.reconcile_token.is_some())
                    || (matches!(
                        self.reconciliation_state,
                        ReconciliationState::ReconciledFailed
                            | ReconciliationState::RequiresConfirmation
                    ) && self.reconcile_token.is_none())
                {
                    return invalid("failed receipt has an invalid reconciliation state");
                }
            }
            ActionStatus::Cancelled => {
                if self.dispatch_state != DispatchState::Rejected
                    || self.unknown
                    || self.unknown_reason.is_some()
                    || self.reconciliation_state != ReconciliationState::NotRequired
                    || self.completion_source != CompletionSource::Ledger
                    || self.error_code != Some(ErrorCode::Cancelled)
                    || self.retryable
                    || self.reconcile_token.is_some()
                    || self.next_action != NextAction::None
                {
                    return invalid("cancelled receipt has an invalid outcome state");
                }
            }
            ActionStatus::Unknown => {
                if !matches!(
                    self.dispatch_state,
                    DispatchState::Dispatched | DispatchState::Acknowledged
                ) || !self.unknown
                    || self.unknown_reason.is_none()
                    || !matches!(
                        self.reconciliation_state,
                        ReconciliationState::Required
                            | ReconciliationState::InProgress
                            | ReconciliationState::RequiresConfirmation
                    )
                    || self.completion_source != CompletionSource::None
                    || self.error_code != Some(ErrorCode::UnknownOutcome)
                    || self.retryable
                    || self.next_action != NextAction::Reconcile
                    || self.reconcile_token.is_none()
                    || self.completed_at.is_some()
                {
                    return invalid("unknown receipt must require reconciliation");
                }
            }
        }
        Ok(())
    }

    /// Validate invariants and expose a stable core error for protocol callers.
    pub fn validate(&self) -> Result<(), CoreError> {
        self.validate_invariants()
            .map_err(|error| CoreError::invalid_argument(error.to_string()))
    }

    /// Ensure the receipt still belongs to the current lease epoch.
    pub fn validate_lease(&self, current: LeaseEpoch) -> Result<(), CoreError> {
        if self.lease_epoch != current {
            return Err(CoreError::stale_lease(
                current.get(),
                self.lease_epoch.get(),
            ));
        }
        Ok(())
    }

    /// Validate the receipt against a durable lease at a specific core time.
    pub fn validate_lease_at(&self, lease: &Lease, now: Timestamp) -> Result<(), CoreError> {
        lease.validate_epoch_at(self.lease_epoch, now)
    }

    /// Mark the action as dispatched across the execution boundary.
    pub fn mark_dispatched(&mut self) -> Result<(), ActionTransitionError> {
        self.require_status(ActionStatus::Queued)?;
        self.status = ActionStatus::Running;
        self.dispatch_state = DispatchState::Dispatched;
        Ok(())
    }

    /// Mark dispatch as acknowledged while execution remains in progress.
    pub fn mark_acknowledged(&mut self) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running || self.dispatch_state != DispatchState::Dispatched
        {
            return Err(ActionTransitionError::InvalidDispatchState);
        }
        self.dispatch_state = DispatchState::Acknowledged;
        Ok(())
    }

    /// Mark a definitive successful completion.
    pub fn mark_succeeded(
        &mut self,
        completed_at: Option<Timestamp>,
        source: CompletionSource,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running {
            return Err(ActionTransitionError::InvalidStatus {
                expected: ActionStatus::Running,
                actual: self.status,
            });
        }
        if !matches!(
            source,
            CompletionSource::Extension
                | CompletionSource::Reconciliation
                | CompletionSource::Ledger
        ) {
            return Err(ActionTransitionError::InvalidCompletionSource);
        }
        self.status = ActionStatus::Succeeded;
        self.retryable = false;
        self.unknown = false;
        self.unknown_reason = None;
        self.reconciliation_state = ReconciliationState::NotRequired;
        self.completion_source = source;
        self.next_action = NextAction::None;
        self.completed_at = completed_at;
        Ok(())
    }

    /// Mark a definitive failed completion.
    pub fn mark_failed(
        &mut self,
        code: ErrorCode,
        retryable: bool,
        completed_at: Option<Timestamp>,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running {
            return Err(ActionTransitionError::InvalidStatus {
                expected: ActionStatus::Running,
                actual: self.status,
            });
        }
        self.status = ActionStatus::Failed;
        self.retryable = retryable;
        self.error_code = Some(code);
        self.completion_source = CompletionSource::Extension;
        self.next_action = if retryable {
            NextAction::Retry
        } else {
            NextAction::None
        };
        self.completed_at = completed_at;
        Ok(())
    }

    /// Mark a pre-dispatch action cancelled.
    pub fn cancel(&mut self, completed_at: Option<Timestamp>) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Queued {
            return Err(ActionTransitionError::InvalidStatus {
                expected: ActionStatus::Queued,
                actual: self.status,
            });
        }
        self.status = ActionStatus::Cancelled;
        self.dispatch_state = DispatchState::Rejected;
        self.error_code = Some(ErrorCode::Cancelled);
        self.completion_source = CompletionSource::Ledger;
        self.next_action = NextAction::None;
        self.completed_at = completed_at;
        Ok(())
    }

    /// Record an unknown post-dispatch outcome without replaying the operation.
    pub fn mark_unknown(
        &mut self,
        reason: UnknownReason,
        reconcile_token: ReconcileToken,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running
            || !matches!(
                self.dispatch_state,
                DispatchState::Dispatched | DispatchState::Acknowledged
            )
        {
            return Err(ActionTransitionError::NotDispatched);
        }
        self.status = ActionStatus::Unknown;
        self.unknown = true;
        self.unknown_reason = Some(reason);
        self.reconciliation_state = ReconciliationState::Required;
        self.reconcile_token = Some(reconcile_token);
        self.error_code = Some(ErrorCode::UnknownOutcome);
        self.retryable = false;
        self.next_action = NextAction::Reconcile;
        Ok(())
    }

    /// Start reconciliation of an unknown receipt.
    pub fn begin_reconciliation(&mut self) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Unknown
            || self.reconciliation_state != ReconciliationState::Required
        {
            return Err(ActionTransitionError::ReconciliationNotRequired);
        }
        self.reconciliation_state = ReconciliationState::InProgress;
        Ok(())
    }

    /// Reconcile an unknown receipt as succeeded without replaying it.
    pub fn reconcile_succeeded(
        &mut self,
        completed_at: Option<Timestamp>,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Unknown
            || self.reconciliation_state != ReconciliationState::InProgress
        {
            return Err(ActionTransitionError::ReconciliationNotInProgress);
        }
        self.status = ActionStatus::Succeeded;
        self.unknown = false;
        self.unknown_reason = None;
        self.retryable = false;
        self.reconciliation_state = ReconciliationState::ReconciledSucceeded;
        self.completion_source = CompletionSource::Reconciliation;
        self.error_code = None;
        self.next_action = NextAction::None;
        self.completed_at = completed_at;
        Ok(())
    }

    /// Reconcile an unknown receipt as failed or requiring confirmation.
    pub fn reconcile_failed(
        &mut self,
        code: ErrorCode,
        requires_confirmation: bool,
        completed_at: Option<Timestamp>,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Unknown
            || self.reconciliation_state != ReconciliationState::InProgress
        {
            return Err(ActionTransitionError::ReconciliationNotInProgress);
        }
        self.status = ActionStatus::Failed;
        self.unknown = false;
        self.unknown_reason = None;
        self.retryable = false;
        self.reconciliation_state = if requires_confirmation {
            ReconciliationState::RequiresConfirmation
        } else {
            ReconciliationState::ReconciledFailed
        };
        self.completion_source = CompletionSource::Reconciliation;
        self.error_code = Some(code);
        self.next_action = if requires_confirmation {
            NextAction::Confirm
        } else {
            NextAction::None
        };
        self.completed_at = completed_at;
        Ok(())
    }

    /// Return whether another mutation must reconcile this receipt first.
    pub const fn requires_reconciliation(&self) -> bool {
        matches!(
            self.reconciliation_state,
            ReconciliationState::Required
                | ReconciliationState::InProgress
                | ReconciliationState::RequiresConfirmation
        )
    }

    fn require_status(&self, expected: ActionStatus) -> Result<(), ActionTransitionError> {
        if self.status == expected {
            Ok(())
        } else {
            Err(ActionTransitionError::InvalidStatus {
                expected,
                actual: self.status,
            })
        }
    }
}

/// Invalid action receipt invariant.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ActionReceiptValidationError {
    /// The receipt contains a state combination that cannot be trusted.
    #[error("invalid action receipt invariant: {message}")]
    Invariant {
        /// Stable explanation of the violated invariant.
        message: String,
    },
}

/// Invalid receipt transition.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ActionTransitionError {
    /// The receipt was not in the expected status.
    #[error("invalid action status: expected {expected:?}, got {actual:?}")]
    InvalidStatus {
        /// Expected state.
        expected: ActionStatus,
        /// Actual state.
        actual: ActionStatus,
    },
    /// Dispatch has not crossed the execution boundary.
    #[error("action was not dispatched")]
    NotDispatched,
    /// Dispatch cannot be acknowledged from this state.
    #[error("invalid dispatch state")]
    InvalidDispatchState,
    /// A definitive completion must identify a completion source.
    #[error("definitive action completion requires a completion source")]
    InvalidCompletionSource,
    /// Reconciliation was not requested.
    #[error("action does not require reconciliation")]
    ReconciliationNotRequired,
    /// Reconciliation has not started.
    #[error("action reconciliation is not in progress")]
    ReconciliationNotInProgress,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        ids::{
            ActionId, ConnectionEpoch, ConnectionNonce, ProfileBindingId, RequestId,
            UserIntentTicketId,
        },
        records::UserIntentTicket,
        states::UserIntentTicketState,
    };

    fn receipt() -> ActionReceipt {
        ActionReceipt::queued(
            ActionId::from_suffix("one").expect("action"),
            RequestId::from_suffix("one").expect("request"),
            IdempotencyKey::from_suffix("one").expect("idempotency"),
            ContentHash::from_bytes(b"request"),
            SpaceId::from_suffix("one").expect("space"),
            Some(PageId::from_suffix("one").expect("page")),
            LeaseEpoch::new(1),
            ActionOperation::Click,
            None,
            Some(Timestamp::new(1)),
        )
    }

    #[test]
    fn dispatched_unknown_receipts_require_reconcile_without_replay() {
        let mut receipt = receipt();
        receipt.mark_dispatched().expect("dispatch");
        receipt
            .mark_unknown(
                UnknownReason::LostResponse,
                ReconcileToken::from_suffix("one").expect("token"),
            )
            .expect("unknown");
        assert_eq!(receipt.status, ActionStatus::Unknown);
        assert!(receipt.requires_reconciliation());
        assert_eq!(receipt.next_action, NextAction::Reconcile);
        receipt.begin_reconciliation().expect("begin");
        receipt
            .reconcile_succeeded(Some(Timestamp::new(2)))
            .expect("reconcile");
        assert_eq!(receipt.status, ActionStatus::Succeeded);
        assert_eq!(receipt.completion_source, CompletionSource::Reconciliation);
    }

    #[test]
    fn stale_lease_is_a_stable_error() {
        let receipt = receipt();
        let error = receipt
            .validate_lease(LeaseEpoch::new(2))
            .expect_err("stale lease");
        assert_eq!(error.code, ErrorCode::StaleLease);
        assert_eq!(error.guidance, crate::errors::ErrorGuidance::RefreshLease);
    }

    #[test]
    fn canonical_action_bytes_are_exact_and_scope_sensitive() {
        let space = SpaceId::from_suffix("one").expect("space");
        let page = PageId::from_suffix("main").expect("page");
        let mut payload = BTreeMap::new();
        payload.insert("label".to_owned(), "go".to_owned());
        payload.insert("count".to_owned(), "2".to_owned());
        let bytes = canonical_action_bytes(ActionOperation::Click, &payload, &space, Some(&page))
            .expect("canonical bytes");
        assert_eq!(
            String::from_utf8(bytes).expect("utf8"),
            r#"{"operation":"click","payload":{"count":"2","label":"go"},"scope":{"page_id":"page_main","space_id":"space_one"}}"#
        );
        let baseline = canonical_action_hash(ActionOperation::Click, &payload, &space, Some(&page))
            .expect("baseline hash");
        assert_eq!(
            baseline,
            canonical_action_hash(ActionOperation::Click, &payload, &space, Some(&page))
                .expect("repeat hash")
        );
        let mut changed_payload = payload.clone();
        changed_payload.insert("count".to_owned(), "3".to_owned());
        assert_ne!(
            baseline,
            canonical_action_hash(
                ActionOperation::Click,
                &changed_payload,
                &space,
                Some(&page)
            )
            .expect("payload hash")
        );
        assert_ne!(
            baseline,
            canonical_action_hash(ActionOperation::Input, &payload, &space, Some(&page))
                .expect("operation hash")
        );
        assert_ne!(
            baseline,
            canonical_action_hash(
                ActionOperation::Click,
                &payload,
                &SpaceId::from_suffix("other").expect("space"),
                Some(&page),
            )
            .expect("space hash")
        );
        assert_ne!(
            baseline,
            canonical_action_hash(
                ActionOperation::Click,
                &payload,
                &space,
                Some(&PageId::from_suffix("other").expect("page")),
            )
            .expect("page hash")
        );
        assert!(canonical_action_bytes(ActionOperation::Click, &f64::NAN, &space, None).is_err());
    }

    #[test]
    fn action_receipt_invariants_reject_tampering_and_accept_transitions() {
        let queued = receipt();
        queued.validate_invariants().expect("queued invariant");

        let mut running = receipt();
        running.mark_dispatched().expect("dispatch");
        running.validate_invariants().expect("running invariant");
        assert!(matches!(
            running.mark_succeeded(Some(Timestamp::new(2)), CompletionSource::None),
            Err(ActionTransitionError::InvalidCompletionSource)
        ));
        running
            .mark_succeeded(Some(Timestamp::new(2)), CompletionSource::Extension)
            .expect("success");
        running.validate().expect("success invariant");

        let mut invalid = queued.clone();
        invalid.status = ActionStatus::Succeeded;
        invalid.dispatch_state = DispatchState::Dispatched;
        invalid.completion_source = CompletionSource::Extension;
        invalid.unknown = true;
        assert!(invalid.validate_invariants().is_err());
        invalid.unknown = false;
        invalid.completion_source = CompletionSource::Timeout;
        assert!(invalid.validate_invariants().is_err());

        let mut invalid_token = queued;
        invalid_token.reconcile_token =
            Some(ReconcileToken::from_suffix("unexpected").expect("token"));
        assert!(invalid_token.validate_invariants().is_err());

        let mut cancelled = receipt();
        cancelled.cancel(Some(Timestamp::new(2))).expect("cancel");
        cancelled.validate().expect("cancel invariant");

        let mut unknown = receipt();
        unknown.mark_dispatched().expect("dispatch");
        unknown
            .mark_unknown(
                UnknownReason::LostResponse,
                ReconcileToken::from_suffix("invariant").expect("token"),
            )
            .expect("unknown");
        unknown.validate().expect("unknown invariant");
    }

    #[test]
    fn action_request_requires_and_consumes_only_matching_confirmation_ticket() {
        let action = receipt();
        let request = ActionRequest {
            request_id: action.request_id,
            action_id: action.action_id,
            idempotency_key: action.idempotency_key,
            request_hash: action.request_hash.clone(),
            space_id: action.space_id,
            page_id: action.page_id.clone(),
            lease_epoch: action.lease_epoch,
            operation: action.operation,
            payload: BTreeMap::<String, String>::new(),
            postcondition: None,
        };
        let profile = ProfileBindingId::from_suffix("default").expect("profile");
        assert_eq!(
            request
                .validate_user_intent(
                    None,
                    true,
                    &profile,
                    ConnectionEpoch::new(1),
                    &ConnectionNonce::from_suffix("panel").expect("nonce"),
                    Some(Generation::new(2)),
                    Timestamp::new(10),
                )
                .expect_err("required ticket")
                .code,
            ErrorCode::PermissionDenied
        );
        request
            .validate_user_intent(
                None,
                false,
                &profile,
                ConnectionEpoch::new(1),
                &ConnectionNonce::from_suffix("panel").expect("nonce"),
                Some(Generation::new(2)),
                Timestamp::new(10),
            )
            .expect("optional ticket for ordinary operation");

        let mut ticket = UserIntentTicket {
            ticket_id: UserIntentTicketId::from_suffix("action_1").expect("ticket"),
            profile_binding_id: profile.clone(),
            space_id: request.space_id.clone(),
            page_id: request.page_id.clone(),
            document_generation: Some(Generation::new(2)),
            action_hash: request.request_hash.clone(),
            lease_epoch: request.lease_epoch,
            connection_epoch: ConnectionEpoch::new(1),
            connection_nonce: ConnectionNonce::from_suffix("panel").expect("nonce"),
            expires_at: Timestamp::new(20),
            state: UserIntentTicketState::Issued,
        };
        request
            .validate_user_intent(
                Some(&mut ticket),
                true,
                &profile,
                ConnectionEpoch::new(1),
                &ConnectionNonce::from_suffix("panel").expect("nonce"),
                Some(Generation::new(2)),
                Timestamp::new(10),
            )
            .expect("matching ticket");
        assert_eq!(ticket.state, UserIntentTicketState::Consumed);
    }
}
