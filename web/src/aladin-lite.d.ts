// aladin-lite ships no type declarations; skymap.ts is the only module that touches it.
declare module "aladin-lite" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const A: any;
  export default A;
}
