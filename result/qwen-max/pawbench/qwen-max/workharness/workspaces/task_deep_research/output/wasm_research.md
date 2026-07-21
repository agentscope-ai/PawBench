# WebAssembly (Wasm) Adoption Outside the Browser

## 1. Current Adoption

### Major Platforms and Companies Using Wasm Outside the Browser

- **Cloudflare**: Cloudflare uses WebAssembly on their edge network to run custom code. [Source](https://blog.cloudflare.com/announcing-workers-unbound)
- **Fastly**: Fastly leverages WebAssembly for its Compute@Edge platform. [Source](https://www.fastly.com/blog/introducing-compute-edge)
- **Deno**: Deno, a modern runtime for JavaScript and TypeScript, supports WebAssembly. [Source](https://deno.land/manual/runtime/webassembly)
- **Shopify**: Shopify uses WebAssembly for server-side rendering and other backend tasks. [Source](https://engineering.shopify.com/blogs/engineering/shopify-wasm)
- **Microsoft Azure**: Azure Functions can now run WebAssembly modules. [Source](https://azure.microsoft.com/en-us/updates/azure-functions-support-for-webassembly-preview/)

## 2. Key Runtimes

- **Wasmtime**: Developed by Mozilla, Wasmtime is a standalone runtime for WebAssembly. It focuses on security, performance, and broad compatibility. [Source](https://wasmtime.dev/)
- **Wasmer**: Wasmer is another popular runtime that aims to be fast, secure, and easy to use. It is sponsored by Wasmer Inc. [Source](https://wasmer.io/)
- **WasmEdge**: WasmEdge is a lightweight, high-performance, and secure runtime for WebAssembly. It is part of the CNCF (Cloud Native Computing Foundation). [Source](https://wasmedge.org/)

## 3. WASI Status

The WebAssembly System Interface (WASI) provides a standard interface for WebAssembly modules to interact with the host environment. As of the latest updates, the following proposals are stable:
- **WASI-NN**: Neural Network Inference API. [Source](https://github.com/WebAssembly/wasi-nn)
- **WASI-Crypto**: Cryptographic Primitives API. [Source](https://github.com/WebAssembly/wasi-crypto)

Proposals in progress include:
- **WASI-HTTP**: HTTP Client API. [Source](https://github.com/WebAssembly/wasi-http)
- **WASI-Threads**: Threading support. [Source](https://github.com/WebAssembly/wasi-threads)

## 4. Use Cases

1. **Serverless Functions** - **Azure Functions**: Azure Functions can now run WebAssembly modules, enabling more efficient and portable serverless functions. [Source](https://azure.microsoft.com/en-us/updates/azure-functions-support-for-webassembly-preview/)
2. **Edge Computing** - **Cloudflare Workers**: Cloudflare uses WebAssembly on their edge network to run custom code, providing low-latency and scalable solutions. [Source](https://blog.cloudflare.com/announcing-workers-unbound)
3. **Backend Services** - **Shopify**: Shopify uses WebAssembly for server-side rendering and other backend tasks, improving performance and reducing costs. [Source](https://engineering.shopify.com/blogs/engineering/shopify-wasm)
4. **Plugin Systems** - **Firecracker**: Firecracker, a virtual machine monitor (VMM) developed by Amazon, supports WebAssembly for plugin systems. [Source](https://firecracker-microvm.github.io/)
5. **Data Processing** - **Fastly Compute@Edge**: Fastly's Compute@Edge platform uses WebAssembly to enable developers to write and deploy code at the edge, processing data in real-time. [Source](https://www.fastly.com/blog/introducing-compute-edge)

## 5. Limitations

- **Limited Standard Library**: WebAssembly lacks a robust standard library, which can make it difficult to write complex applications. [Source](https://hacks.mozilla.org/2019/07/webassemblys-post-mvp-future/)
- **Garbage Collection**: WebAssembly does not have built-in garbage collection, which can lead to memory management issues. [Source](https://hacks.mozilla.org/2019/07/webassemblys-post-mvp-future/)
- **Debugging Tools**: Debugging tools for WebAssembly are still maturing, making it challenging to diagnose and fix issues. [Source](https://hacks.mozilla.org/2019/07/webassemblys-post-mvp-future/)
- **Threading Support**: While threading is being worked on, it is not yet fully standardized, limiting the ability to write multi-threaded applications. [Source](https://github.com/WebAssembly/wasi-threads)
- **Host Environment Access**: Access to the host environment is limited, and the standardization of WASI is still in progress, which can restrict the types of applications that can be written. [Source](https://hacks.mozilla.org/2019/07/webassemblys-post-mvp-future/)

## References

- [Cloudflare Workers Unbound](https://blog.cloudflare.com/announcing-workers-unbound)
- [Fastly Compute@Edge](https://www.fastly.com/blog/introducing-compute-edge)
- [Deno WebAssembly Support](https://deno.land/manual/runtime/webassembly)
- [Shopify and WebAssembly](https://engineering.shopify.com/blogs/engineering/shopify-wasm)
- [Azure Functions Support for WebAssembly](https://azure.microsoft.com/en-us/updates/azure-functions-support-for-webassembly-preview/)
- [Wasmtime](https://wasmtime.dev/)
- [Wasmer](https://wasmer.io/)
- [WasmEdge](https://wasmedge.org/)
- [WASI-NN](https://github.com/WebAssembly/wasi-nn)
- [WASI-Crypto](https://github.com/WebAssembly/wasi-crypto)
- [WASI-HTTP](https://github.com/WebAssembly/wasi-http)
- [WASI-Threads](https://github.com/WebAssembly/wasi-threads)
- [WebAssembly Post-MVP Future](https://hacks.mozilla.org/2019/07/webassemblys-post-mvp-future/)
- [Firecracker](https://firecracker-microvm.github.io/)