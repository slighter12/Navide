//! Every declared CLI behind one type, for the callers that only have a key
//! (the wire, the JSON export). Generated from the list `build.rs` makes of
//! `src/vendors/`: each arm is that vendor's own compiled observer, so
//! looking one up by key is the only dispatch there is.
use crate::decl::{self, Cli, Form, Vendor};
use crate::observe::{ActivityEvent, Error, Note, Observer, TurnUsage, UsageRead};
use serde_json::{Map, Value};
use std::path::Path;

/// Something done for every declared CLI, generically (`each`).
pub trait Visit {
    fn vendor<V: Vendor>(&mut self, file: &'static str);
}

macro_rules! registry {
    ($($vendor:ident),* $(,)?) => {
        /// Every declared CLI, sorted by key.
        pub static ALL: &[&Cli] = &[$(<crate::vendors::$vendor::V as Vendor>::CLI),*];

        /// Every declaration as JSON, in the same order.
        pub fn export() -> Vec<Value> {
            vec![$(decl::export::<crate::vendors::$vendor::V>()),*]
        }

        /// Call `visitor` once per declared CLI, with its type and the name
        /// of its file under `src/vendors/`.
        pub fn each(visitor: &mut impl Visit) {
            $(visitor.vendor::<crate::vendors::$vendor::V>(stringify!($vendor));)*
        }

        /// One CLI's observer, whichever CLI it is.
        #[allow(non_camel_case_types)]
        pub enum AnyObserver {
            $($vendor(Observer<crate::vendors::$vendor::V>)),*
        }

        impl AnyObserver {
            /// An observer for every CLI that declares its records.
            pub fn all() -> Vec<AnyObserver> {
                let mut out = Vec::new();
                $(
                    if let Some(observer) = Observer::<crate::vendors::$vendor::V>::new() {
                        out.push(AnyObserver::$vendor(observer));
                    }
                )*
                out
            }

            pub fn key(&self) -> &'static str {
                match self { $(AnyObserver::$vendor(observer) => observer.key()),* }
            }

            pub fn cwd_form(&self) -> Form {
                match self { $(AnyObserver::$vendor(observer) => observer.spec().cwd_form),* }
            }

            pub fn set_clock(&mut self, now: Option<f64>) {
                match self { $(AnyObserver::$vendor(observer) => observer.set_clock(now)),* }
            }

            pub fn usage(&mut self, path: &Path, checkpoint: &Map<String, Value>) -> Result<UsageRead, Error> {
                match self { $(AnyObserver::$vendor(observer) => observer.usage(path, checkpoint)),* }
            }

            pub fn activity_bag(&mut self, path: &Path, bag: &mut Vec<String>) -> Result<Vec<ActivityEvent>, Error> {
                match self { $(AnyObserver::$vendor(observer) => observer.activity_bag(path, bag)),* }
            }

            pub fn turns(&mut self, path: &Path, session_id: &str) -> Result<Vec<TurnUsage>, Error> {
                match self { $(AnyObserver::$vendor(observer) => observer.turns(path, session_id)),* }
            }

            pub fn take_notes(&mut self) -> Vec<Note> {
                match self { $(AnyObserver::$vendor(observer) => observer.take_notes()),* }
            }
        }
    };
}

each_vendor!(registry);

/// The observer of one CLI by key (`None`: unknown, or it declares no records).
pub fn observer(key: &str) -> Option<AnyObserver> {
    AnyObserver::all().into_iter().find(|observer| observer.key() == key)
}
