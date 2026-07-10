/**
 * Thin Emscripten host for the C++ mod ABI.
 *
 * A mod core is resolved by import name once at load time. During emulation a
 * HOST16 trap therefore performs only two numeric map/array lookups before
 * invoking TypeScript. Host functions must be synchronous.
 */

export interface EmscriptenGbCore {
  HEAPU8: Uint8Array;
  HEAPU32: Uint32Array;
  _malloc(size: number): number;
  _free(pointer: number): void;
  cwrap(
    name: string,
    returnType: "number" | "string" | null,
    argumentTypes: string[],
  ): (...args: number[]) => number | string | null;
  addFunction(callback: (...args: number[]) => void, signature: string): number;
  removeFunction(pointer: number): void;
}

export interface ModMemory {
  read8(address: number): number;
  write8(address: number, value: number): void;
}

export class ModCpuContext {
  constructor(
    private readonly module: EmscriptenGbCore,
    private readonly pointer: number,
  ) {}

  private get view(): DataView {
    // Emscripten may replace the ArrayBuffer when memory grows.
    return new DataView(this.module.HEAPU8.buffer);
  }

  private read16(offset: number): number {
    return this.view.getUint16(this.pointer + offset, true);
  }

  private write16(offset: number, value: number): void {
    this.view.setUint16(this.pointer + offset, value & 0xffff, true);
  }

  get af(): number { return this.read16(4); }
  set af(value: number) { this.write16(4, value & 0xfff0); }
  get bc(): number { return this.read16(6); }
  set bc(value: number) { this.write16(6, value); }
  get de(): number { return this.read16(8); }
  set de(value: number) { this.write16(8, value); }
  get hl(): number { return this.read16(10); }
  set hl(value: number) { this.write16(10, value); }
  get sp(): number { return this.read16(12); }
  set sp(value: number) { this.write16(12, value); }
  get pc(): number { return this.read16(14); }
  set pc(value: number) { this.write16(14, value); }

  get ime(): boolean { return this.view.getUint8(this.pointer + 16) !== 0; }
  set ime(value: boolean) { this.view.setUint8(this.pointer + 16, value ? 1 : 0); }
  get halted(): boolean { return this.view.getUint8(this.pointer + 17) !== 0; }
  set halted(value: boolean) { this.view.setUint8(this.pointer + 17, value ? 1 : 0); }

  get a(): number { return this.af >>> 8; }
  set a(value: number) { this.af = ((value & 0xff) << 8) | (this.af & 0xf0); }
  get flags(): number { return this.af & 0xf0; }
  set flags(value: number) { this.af = (this.af & 0xff00) | (value & 0xf0); }
}

export type ModHostFunction = (cpu: ModCpuContext, memory: ModMemory) => void;
export type TypeScriptModCore = Readonly<Record<string, ModHostFunction>>;

export type LoadedMod = {
  handle: number;
  id: string;
  name: string;
  imports: readonly string[];
};

type Api = {
  loadSymbols(gb: number, text: number, length: number): number;
  load(gb: number, data: number, length: number, outHandle: number): number;
  unload(gb: number, handle: number): number;
  id(gb: number, handle: number): string | null;
  name(gb: number, handle: number): string | null;
  importCount(gb: number, handle: number): number;
  importName(gb: number, handle: number, index: number): string | null;
  lastError(gb: number): string | null;
  setHostCallback(gb: number, callback: number, user: number): void;
  read8(gb: number, address: number): number;
  write8(gb: number, address: number, value: number): void;
};

export class GbModRuntime {
  private readonly api: Api;
  private readonly callbackPointer: number;
  private readonly handlers = new Map<number, readonly ModHostFunction[]>();
  private pendingHostError: Error | null = null;
  private disposed = false;

  readonly memory: ModMemory;

  constructor(
    private readonly module: EmscriptenGbCore,
    private readonly gb: number,
  ) {
    const wrap = <T>(name: string, result: "number" | "string" | null, args: string[]) =>
      module.cwrap(name, result, args) as T;
    this.api = {
      loadSymbols: wrap("gb_mod_load_symbols", "number", ["number", "number", "number"]),
      load: wrap("gb_mod_load", "number", ["number", "number", "number", "number"]),
      unload: wrap("gb_mod_unload", "number", ["number", "number"]),
      id: wrap("gb_mod_id", "string", ["number", "number"]),
      name: wrap("gb_mod_name", "string", ["number", "number"]),
      importCount: wrap("gb_mod_import_count", "number", ["number", "number"]),
      importName: wrap("gb_mod_import_name", "string", ["number", "number", "number"]),
      lastError: wrap("gb_mod_last_error", "string", ["number"]),
      setHostCallback: wrap("gb_mod_set_host_callback", null, ["number", "number", "number"]),
      read8: wrap("gb_read_mem", "number", ["number", "number"]),
      write8: wrap("gb_write_mem", null, ["number", "number", "number"]),
    };
    this.memory = {
      read8: (address) => this.api.read8(this.gb, address & 0xffff),
      write8: (address, value) => this.api.write8(this.gb, address & 0xffff, value & 0xff),
    };
    this.callbackPointer = module.addFunction(
      (gb, handle, importIndex, context, _user) =>
        this.dispatch(gb, handle, importIndex, context),
      "viiiii",
    );
    this.api.setHostCallback(this.gb, this.callbackPointer, 0);
  }

  loadSymbols(symbolDocument: string): void {
    this.ensureActive();
    const bytes = new TextEncoder().encode(symbolDocument);
    this.withBytes(bytes, (pointer) =>
      this.check(this.api.loadSymbols(this.gb, pointer, bytes.length)));
  }

  loadPackage(packageBytes: Uint8Array, core: TypeScriptModCore): LoadedMod {
    this.ensureActive();
    const outHandle = this.module._malloc(4);
    try {
      this.withBytes(packageBytes, (pointer) =>
        this.check(this.api.load(this.gb, pointer, packageBytes.length, outHandle)));
      const handle = new DataView(this.module.HEAPU8.buffer).getUint32(outHandle, true);
      try {
        const imports = this.resolveImports(handle, core);
        this.handlers.set(handle, imports.handlers);
        return {
          handle,
          id: this.api.id(this.gb, handle) ?? "",
          name: this.api.name(this.gb, handle) ?? "",
          imports: imports.names,
        };
      } catch (error) {
        this.api.unload(this.gb, handle);
        throw error;
      }
    } finally {
      this.module._free(outHandle);
    }
  }

  unload(handle: number): void {
    this.ensureActive();
    this.check(this.api.unload(this.gb, handle));
    this.handlers.delete(handle);
  }

  /** Re-throws a TypeScript exception captured inside a synchronous host trap. */
  throwPendingHostError(): void {
    const error = this.pendingHostError;
    this.pendingHostError = null;
    if (error) throw error;
  }

  dispose(): void {
    if (this.disposed) return;
    this.api.setHostCallback(this.gb, 0, 0);
    this.module.removeFunction(this.callbackPointer);
    this.handlers.clear();
    this.disposed = true;
  }

  private resolveImports(handle: number, core: TypeScriptModCore): {
    names: string[];
    handlers: ModHostFunction[];
  } {
    const names: string[] = [];
    const handlers: ModHostFunction[] = [];
    const count = this.api.importCount(this.gb, handle);
    for (let index = 0; index < count; index += 1) {
      const name = this.api.importName(this.gb, handle, index);
      if (!name) throw new Error(`Mod import ${index} has no name`);
      const handler = core[name];
      if (!handler) throw new Error(`TypeScript core does not export '${name}'`);
      names.push(name);
      handlers.push(handler);
    }
    return { names, handlers };
  }

  private dispatch(gb: number, handle: number, importIndex: number, context: number): void {
    if (gb !== this.gb) return;
    const handler = this.handlers.get(handle)?.[importIndex];
    if (!handler) {
      this.pendingHostError = new Error(
        `No TypeScript handler for mod ${handle}, import ${importIndex}`,
      );
      return;
    }
    try {
      handler(new ModCpuContext(this.module, context), this.memory);
    } catch (error) {
      this.pendingHostError = error instanceof Error ? error : new Error(String(error));
    }
  }

  private withBytes<T>(bytes: Uint8Array, operation: (pointer: number) => T): T {
    const pointer = this.module._malloc(Math.max(bytes.length, 1));
    try {
      this.module.HEAPU8.set(bytes, pointer);
      return operation(pointer);
    } finally {
      this.module._free(pointer);
    }
  }

  private check(status: number): void {
    if (status !== 0) {
      throw new Error(this.api.lastError(this.gb) ?? `Mod operation failed (${status})`);
    }
  }

  private ensureActive(): void {
    if (this.disposed) throw new Error("GbModRuntime has been disposed");
  }
}
