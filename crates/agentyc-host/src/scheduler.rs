//! Bounded admission and serialization for host operations.

use std::{
    collections::{BTreeMap, VecDeque},
    sync::{Arc, Condvar, Mutex, MutexGuard},
    time::{Duration, Instant},
};

use agentyc_core::SpaceId;

use crate::waits::CancellationToken;

/// Concurrency and queue bounds for a [`Scheduler`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SchedulerLimits {
    /// Maximum number of read operations holding permits at once.
    pub max_concurrent_reads: usize,
    /// Maximum number of reads waiting for a permit.
    pub max_queued_reads: usize,
    /// Maximum number of mutations waiting across all spaces.
    pub max_queued_mutations: usize,
    /// Maximum number of mutations waiting per space.
    pub max_queued_mutations_per_space: usize,
}

impl SchedulerLimits {
    /// Construct explicit limits. Queue limits may be zero to reject waiting work.
    pub const fn new(
        max_concurrent_reads: usize,
        max_queued_reads: usize,
        max_queued_mutations: usize,
        max_queued_mutations_per_space: usize,
    ) -> Self {
        Self {
            max_concurrent_reads,
            max_queued_reads,
            max_queued_mutations,
            max_queued_mutations_per_space,
        }
    }
}

impl Default for SchedulerLimits {
    fn default() -> Self {
        Self::new(8, 64, 256, 32)
    }
}

/// The reason an operation could not enter its bounded wait queue.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackpressureKind {
    /// The global read wait queue is full.
    ReadQueueFull,
    /// The target space's mutation wait queue is full.
    MutationQueueFull,
    /// The global mutation wait queue is full.
    GlobalMutationQueueFull,
}

/// Explicit rejection returned when the corresponding wait queue is full.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Backpressure {
    /// Queue that reached its configured bound.
    pub kind: BackpressureKind,
}

/// A bounded scheduler wait can be rejected without acquiring a permit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SchedulerWaitError {
    /// The corresponding queue has reached its configured bound.
    Backpressure(Backpressure),
    /// The caller cancelled while waiting.
    Cancelled,
    /// The caller's absolute deadline elapsed while waiting.
    DeadlineExceeded,
}

/// A point-in-time view of scheduler occupancy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SchedulerSnapshot {
    /// Read permits currently held.
    pub active_reads: usize,
    /// Reads waiting for a permit.
    pub queued_reads: usize,
    /// Mutations currently holding their per-space permit.
    pub active_mutations: usize,
    /// Mutations waiting across all spaces.
    pub queued_mutations: usize,
}

/// Bounded host operation admission, independent of any broker implementation.
#[derive(Debug, Clone)]
pub struct Scheduler {
    inner: Arc<Inner>,
}

#[derive(Debug)]
struct Inner {
    limits: SchedulerLimits,
    state: Mutex<State>,
    changed: Condvar,
}

#[derive(Debug, Default)]
struct State {
    active_reads: usize,
    read_queue: VecDeque<Arc<()>>,
    mutations: BTreeMap<SpaceId, MutationQueue>,
}

#[derive(Debug, Default)]
struct MutationQueue {
    active: bool,
    waiting: VecDeque<Arc<()>>,
}

impl Scheduler {
    /// Create a scheduler. At least one read slot is required to avoid permanent
    /// blocking of every read operation.
    pub fn new(limits: SchedulerLimits) -> Result<Self, &'static str> {
        if limits.max_concurrent_reads == 0 {
            return Err("max_concurrent_reads must be greater than zero");
        }
        Ok(Self {
            inner: Arc::new(Inner {
                limits,
                state: Mutex::new(State::default()),
                changed: Condvar::new(),
            }),
        })
    }

    /// Create a scheduler with default bounds.
    pub fn with_defaults() -> Self {
        Self::new(SchedulerLimits::default()).expect("default scheduler limits are valid")
    }

    /// Return the configured bounds.
    pub fn limits(&self) -> SchedulerLimits {
        self.inner.limits
    }

    /// Wait for the next FIFO mutation permit for `space_id`.
    ///
    /// Mutations in different spaces do not block each other. If the per-space
    /// wait queue is full, this returns immediately with explicit backpressure.
    pub fn acquire_mutation(&self, space_id: SpaceId) -> Result<MutationPermit, Backpressure> {
        self.acquire_mutation_inner(space_id, None, None)
            .map_err(|error| match error {
                SchedulerWaitError::Backpressure(backpressure) => backpressure,
                SchedulerWaitError::Cancelled | SchedulerWaitError::DeadlineExceeded => {
                    unreachable!("uncancellable scheduler wait cannot be cancelled")
                }
            })
    }

    /// Wait for a mutation permit while honoring cooperative cancellation.
    pub fn acquire_mutation_cancelable(
        &self,
        space_id: SpaceId,
        cancellation: &CancellationToken,
    ) -> Result<MutationPermit, SchedulerWaitError> {
        self.acquire_mutation_inner(space_id, None, Some(cancellation))
    }

    /// Wait for a mutation permit until an absolute deadline.
    pub fn acquire_mutation_until(
        &self,
        space_id: SpaceId,
        deadline: Instant,
    ) -> Result<MutationPermit, SchedulerWaitError> {
        self.acquire_mutation_inner(space_id, Some(deadline), None)
    }

    fn acquire_mutation_inner(
        &self,
        space_id: SpaceId,
        deadline: Option<Instant>,
        cancellation: Option<&CancellationToken>,
    ) -> Result<MutationPermit, SchedulerWaitError> {
        let mut state = lock(&self.inner.state);
        let queue = state.mutations.entry(space_id.clone()).or_default();
        if !queue.active && queue.waiting.is_empty() {
            queue.active = true;
            return Ok(MutationPermit {
                inner: Arc::clone(&self.inner),
                space_id,
                released: false,
            });
        }
        if queue.waiting.len() >= self.inner.limits.max_queued_mutations_per_space {
            return Err(SchedulerWaitError::Backpressure(Backpressure {
                kind: BackpressureKind::MutationQueueFull,
            }));
        }
        if state
            .mutations
            .values()
            .map(|queue| queue.waiting.len())
            .sum::<usize>()
            >= self.inner.limits.max_queued_mutations
        {
            return Err(SchedulerWaitError::Backpressure(Backpressure {
                kind: BackpressureKind::GlobalMutationQueueFull,
            }));
        }

        let token = Arc::new(());
        state
            .mutations
            .get_mut(&space_id)
            .expect("mutation queue was inserted")
            .waiting
            .push_back(Arc::clone(&token));
        loop {
            if cancellation.is_some_and(CancellationToken::is_cancelled) {
                remove_mutation_waiter(&mut state, &space_id, &token);
                self.inner.changed.notify_all();
                return Err(SchedulerWaitError::Cancelled);
            }
            if deadline.is_some_and(|limit| Instant::now() >= limit) {
                remove_mutation_waiter(&mut state, &space_id, &token);
                self.inner.changed.notify_all();
                return Err(SchedulerWaitError::DeadlineExceeded);
            }
            let is_next = state
                .mutations
                .get(&space_id)
                .and_then(|queue| queue.waiting.front())
                .is_some_and(|front| Arc::ptr_eq(front, &token));
            let queue = state
                .mutations
                .get_mut(&space_id)
                .expect("queued mutation space remains present");
            if !queue.active && is_next {
                queue.waiting.pop_front();
                queue.active = true;
                self.inner.changed.notify_all();
                return Ok(MutationPermit {
                    inner: Arc::clone(&self.inner),
                    space_id,
                    released: false,
                });
            }
            state = wait_for(&self.inner.changed, state, deadline);
        }
    }

    /// Wait for a bounded read permit. Reads are FIFO when queued.
    ///
    /// Returns immediately with backpressure when all read wait slots are full.
    pub fn acquire_read(&self) -> Result<ReadPermit, Backpressure> {
        self.acquire_read_inner(None, None)
            .map_err(|error| match error {
                SchedulerWaitError::Backpressure(backpressure) => backpressure,
                SchedulerWaitError::Cancelled | SchedulerWaitError::DeadlineExceeded => {
                    unreachable!("uncancellable scheduler wait cannot be cancelled")
                }
            })
    }

    /// Wait for a read permit while honoring cooperative cancellation.
    pub fn acquire_read_cancelable(
        &self,
        cancellation: &CancellationToken,
    ) -> Result<ReadPermit, SchedulerWaitError> {
        self.acquire_read_inner(None, Some(cancellation))
    }

    /// Wait for a read permit until an absolute deadline.
    pub fn acquire_read_until(&self, deadline: Instant) -> Result<ReadPermit, SchedulerWaitError> {
        self.acquire_read_inner(Some(deadline), None)
    }

    fn acquire_read_inner(
        &self,
        deadline: Option<Instant>,
        cancellation: Option<&CancellationToken>,
    ) -> Result<ReadPermit, SchedulerWaitError> {
        let mut state = lock(&self.inner.state);
        if state.active_reads < self.inner.limits.max_concurrent_reads
            && state.read_queue.is_empty()
        {
            state.active_reads += 1;
            return Ok(ReadPermit {
                inner: Arc::clone(&self.inner),
                released: false,
            });
        }
        if state.read_queue.len() >= self.inner.limits.max_queued_reads {
            return Err(SchedulerWaitError::Backpressure(Backpressure {
                kind: BackpressureKind::ReadQueueFull,
            }));
        }

        let token = Arc::new(());
        state.read_queue.push_back(Arc::clone(&token));
        loop {
            if cancellation.is_some_and(CancellationToken::is_cancelled) {
                remove_read_waiter(&mut state, &token);
                self.inner.changed.notify_all();
                return Err(SchedulerWaitError::Cancelled);
            }
            if deadline.is_some_and(|limit| Instant::now() >= limit) {
                remove_read_waiter(&mut state, &token);
                self.inner.changed.notify_all();
                return Err(SchedulerWaitError::DeadlineExceeded);
            }
            let is_next = state
                .read_queue
                .front()
                .is_some_and(|front| Arc::ptr_eq(front, &token));
            if state.active_reads < self.inner.limits.max_concurrent_reads && is_next {
                state.read_queue.pop_front();
                state.active_reads += 1;
                self.inner.changed.notify_all();
                return Ok(ReadPermit {
                    inner: Arc::clone(&self.inner),
                    released: false,
                });
            }
            state = wait_for(&self.inner.changed, state, deadline);
        }
    }

    /// Return current active and queued operation counts.
    pub fn snapshot(&self) -> SchedulerSnapshot {
        let state = lock(&self.inner.state);
        SchedulerSnapshot {
            active_reads: state.active_reads,
            queued_reads: state.read_queue.len(),
            active_mutations: state
                .mutations
                .values()
                .filter(|queue| queue.active)
                .count(),
            queued_mutations: state
                .mutations
                .values()
                .map(|queue| queue.waiting.len())
                .sum(),
        }
    }
}

/// Exclusive mutation permit for one logical space.
#[derive(Debug)]
pub struct MutationPermit {
    inner: Arc<Inner>,
    space_id: SpaceId,
    released: bool,
}

impl MutationPermit {
    /// Return the space protected by this permit.
    pub fn space_id(&self) -> &SpaceId {
        &self.space_id
    }
}

impl Drop for MutationPermit {
    fn drop(&mut self) {
        if self.released {
            return;
        }
        let mut state = lock(&self.inner.state);
        if let Some(queue) = state.mutations.get_mut(&self.space_id) {
            queue.active = false;
            if queue.waiting.is_empty() {
                state.mutations.remove(&self.space_id);
            }
        }
        self.released = true;
        self.inner.changed.notify_all();
    }
}

/// Shared read permit, bounded across the scheduler.
#[derive(Debug)]
pub struct ReadPermit {
    inner: Arc<Inner>,
    released: bool,
}

impl Drop for ReadPermit {
    fn drop(&mut self) {
        if !self.released {
            let mut state = lock(&self.inner.state);
            state.active_reads -= 1;
            self.released = true;
            self.inner.changed.notify_all();
        }
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn wait_for<'a, T>(
    changed: &Condvar,
    state: MutexGuard<'a, T>,
    deadline: Option<Instant>,
) -> MutexGuard<'a, T> {
    let wait_duration = deadline
        .map(|limit| limit.saturating_duration_since(Instant::now()))
        .unwrap_or(Duration::from_millis(25))
        .min(Duration::from_millis(25));
    if wait_duration.is_zero() {
        return state;
    }
    changed
        .wait_timeout(state, wait_duration)
        .map(|(state, _)| state)
        .unwrap_or_else(|poisoned| poisoned.into_inner().0)
}

fn remove_mutation_waiter(state: &mut State, space_id: &SpaceId, token: &Arc<()>) {
    if let Some(queue) = state.mutations.get_mut(space_id) {
        if let Some(index) = queue
            .waiting
            .iter()
            .position(|candidate| Arc::ptr_eq(candidate, token))
        {
            queue.waiting.remove(index);
        }
        if !queue.active && queue.waiting.is_empty() {
            state.mutations.remove(space_id);
        }
    }
}

fn remove_read_waiter(state: &mut State, token: &Arc<()>) {
    if let Some(index) = state
        .read_queue
        .iter()
        .position(|candidate| Arc::ptr_eq(candidate, token))
    {
        state.read_queue.remove(index);
    }
}

#[cfg(test)]
mod tests {
    use std::{
        sync::mpsc,
        thread,
        time::{Duration, Instant},
    };

    use super::*;

    fn scheduler(reads: usize, read_queue: usize, mutation_queue: usize) -> Scheduler {
        Scheduler::new(SchedulerLimits::new(
            reads,
            read_queue,
            mutation_queue,
            mutation_queue,
        ))
        .unwrap()
    }

    fn space(name: &str) -> SpaceId {
        SpaceId::from_suffix(name).unwrap()
    }

    fn wait_for(
        snapshot: impl Fn() -> SchedulerSnapshot,
        predicate: impl Fn(SchedulerSnapshot) -> bool,
    ) {
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            let current = snapshot();
            if predicate(current) {
                return;
            }
            thread::yield_now();
        }
        panic!("scheduler did not reach expected queue state");
    }

    #[test]
    fn mutations_are_fifo_per_space_and_independent_between_spaces() {
        let scheduler = scheduler(2, 2, 2);
        let key = space("space-fifo");
        let held = scheduler.acquire_mutation(key.clone()).unwrap();
        let (tx, rx) = mpsc::channel();

        let first_scheduler = scheduler.clone();
        let first_key = key.clone();
        let first_tx = tx.clone();
        let first = thread::spawn(move || {
            let _permit = first_scheduler.acquire_mutation(first_key).unwrap();
            first_tx.send(1).unwrap();
        });
        wait_for(
            || scheduler.snapshot(),
            |current| current.queued_mutations == 1,
        );

        let second_scheduler = scheduler.clone();
        let second_key = key.clone();
        let second = thread::spawn(move || {
            let _permit = second_scheduler.acquire_mutation(second_key).unwrap();
            tx.send(2).unwrap();
        });
        wait_for(
            || scheduler.snapshot(),
            |current| current.queued_mutations == 2,
        );

        let other_space = scheduler
            .acquire_mutation(space("space-independent"))
            .unwrap();
        drop(other_space);
        drop(held);

        assert_eq!(rx.recv_timeout(Duration::from_secs(2)).unwrap(), 1);
        assert_eq!(rx.recv_timeout(Duration::from_secs(2)).unwrap(), 2);
        first.join().unwrap();
        second.join().unwrap();
    }

    #[test]
    fn mutation_queue_full_returns_explicit_backpressure() {
        let scheduler = scheduler(1, 0, 1);
        let key = space("space-bounded");
        let held = scheduler.acquire_mutation(key.clone()).unwrap();
        let queued_scheduler = scheduler.clone();
        let queued_key = key.clone();
        let queued = thread::spawn(move || queued_scheduler.acquire_mutation(queued_key).unwrap());
        wait_for(
            || scheduler.snapshot(),
            |current| current.queued_mutations == 1,
        );

        let rejected = scheduler.acquire_mutation(key).unwrap_err();
        assert_eq!(rejected.kind, BackpressureKind::MutationQueueFull);
        drop(held);
        drop(queued.join().unwrap());
    }

    #[test]
    fn mutation_wait_queue_is_bounded_across_spaces() {
        let scheduler = Scheduler::new(SchedulerLimits::new(1, 0, 1, 2)).unwrap();
        let first_space = space("space-global-one");
        let second_space = space("space-global-two");
        let first_held = scheduler.acquire_mutation(first_space.clone()).unwrap();
        let queued_scheduler = scheduler.clone();
        let queued = thread::spawn(move || queued_scheduler.acquire_mutation(first_space).unwrap());
        wait_for(
            || scheduler.snapshot(),
            |current| current.queued_mutations == 1,
        );

        let second_held = scheduler.acquire_mutation(second_space.clone()).unwrap();
        let rejected = scheduler.acquire_mutation(second_space).unwrap_err();
        assert_eq!(rejected.kind, BackpressureKind::GlobalMutationQueueFull);
        drop(second_held);
        drop(first_held);
        drop(queued.join().unwrap());
    }

    #[test]
    fn read_concurrency_and_wait_queue_are_bounded() {
        let scheduler = scheduler(1, 1, 0);
        let held = scheduler.acquire_read().unwrap();
        let queued_scheduler = scheduler.clone();
        let (tx, rx) = mpsc::channel();
        let queued = thread::spawn(move || {
            let _permit = queued_scheduler.acquire_read().unwrap();
            tx.send(()).unwrap();
        });
        wait_for(|| scheduler.snapshot(), |current| current.queued_reads == 1);

        let rejected = scheduler.acquire_read().unwrap_err();
        assert_eq!(rejected.kind, BackpressureKind::ReadQueueFull);
        drop(held);
        rx.recv_timeout(Duration::from_secs(2)).unwrap();
        queued.join().unwrap();
        assert_eq!(scheduler.snapshot().active_reads, 0);
    }

    #[test]
    fn cancelled_mutation_waiter_is_removed_and_next_waiter_progresses() {
        let scheduler = scheduler(1, 0, 2);
        let key = space("space-cancel-mutation");
        let held = scheduler.acquire_mutation(key.clone()).unwrap();
        let cancellation = CancellationToken::new();
        let waiting_scheduler = scheduler.clone();
        let waiting_cancellation = cancellation.clone();
        let waiting_key = key.clone();
        let cancelled = thread::spawn(move || {
            waiting_scheduler.acquire_mutation_cancelable(waiting_key, &waiting_cancellation)
        });
        wait_for(
            || scheduler.snapshot(),
            |current| current.queued_mutations == 1,
        );
        cancellation.cancel();
        assert!(matches!(
            cancelled.join().unwrap(),
            Err(SchedulerWaitError::Cancelled)
        ));
        assert_eq!(scheduler.snapshot().queued_mutations, 0);
        drop(held);
        let permit = scheduler.acquire_mutation(key).unwrap();
        drop(permit);
    }

    #[test]
    fn deadline_expires_a_read_waiter_without_leaking_a_permit() {
        let scheduler = scheduler(1, 1, 0);
        let held = scheduler.acquire_read().unwrap();
        let result = scheduler.acquire_read_until(Instant::now() + Duration::from_millis(30));
        assert!(matches!(result, Err(SchedulerWaitError::DeadlineExceeded)));
        assert_eq!(scheduler.snapshot().queued_reads, 0);
        drop(held);
        assert_eq!(scheduler.snapshot().active_reads, 0);
    }

    #[test]
    fn zero_read_concurrency_is_rejected() {
        assert!(Scheduler::new(SchedulerLimits::new(0, 0, 0, 0)).is_err());
    }
}
