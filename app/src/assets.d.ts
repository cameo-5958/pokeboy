declare module "react-native/Libraries/Image/resolveAssetSource" {
  interface ResolvedAssetSource {
    __packager_asset: boolean;
    uri: string;
    width: number | null;
    height: number | null;
    scale: number;
  }
  function resolveAssetSource(source: number): ResolvedAssetSource | null;
  function resolveAssetSource(source: { uri: string }): ResolvedAssetSource | null;
  export default resolveAssetSource;
}

declare module "*.html" {
  const asset: number;
  export default asset;
}

declare module "*.bin" {
  const asset: number;
  export default asset;
}

declare module "*.wasm" {
  const asset: number;
  export default asset;
}
