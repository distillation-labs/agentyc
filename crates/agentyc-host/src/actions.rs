//! Host-facing action journal result types.

use agentyc_core::{ActionId, ActionReceipt};

/// Result of an action submission or execution step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionResult {
    /// Durable action receipt after the step.
    pub receipt: ActionReceipt,
}

/// A bounded status lookup key used by later protocol adapters.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionLookup {
    /// Durable action identity.
    pub action_id: ActionId,
}
