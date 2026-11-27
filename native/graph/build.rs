//! Gives the macOS library a relocatable install name instead of the absolute
//! path of the build directory, so it can be shipped and loaded from anywhere.
fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-Wl,-install_name,@rpath/libpraukron_graph.dylib");
    }
}
