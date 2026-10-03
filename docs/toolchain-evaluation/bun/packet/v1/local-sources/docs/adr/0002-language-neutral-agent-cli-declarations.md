# Use language-neutral declarations for shared Agent CLI facts

Shared declarative Agent CLI facts will have one language-neutral authority, with typed data generated for TypeScript and Python and, if adopted, Rust. Keeping separate handwritten declarations would preserve familiar ownership but allow launch paths and capability checks to disagree; choosing either TypeScript or Python as the sole authority would couple the shared model to one implementation language.

Only facts that are naturally data belong in these declarations. Complex parsing and vendor-native strategies remain executable adapters rather than a configuration DSL. Host surfaces may expose different subsets of the catalog without redefining vendor facts or widening Plugin authority. This is an agreed design direction, not an implemented contract or migration authorization.
