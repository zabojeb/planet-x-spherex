"""Core library of the Planet X x SPHEREx viewer. No web framework imports live here.

Module map
----------
config        runtime settings, overridable with SPHEREX_* environment variables
bands         detectors D1-D6, their wavelength ranges, the native pixel scale
timeutil      MJD / Julian year / display-string conversions
flags         FLAGS bitmask names, the masks the pipeline applies, MSKN/MSKD header parsing
cache         every on-disk cache location
errors        exceptions shared by the library and its adapters
targets/      Target model with proper motion, challenge presets, coordinate parsing, name resolution
archive/      generic TAP client and the SPHEREx Level 2 image search
io/           HTTP range client, FITS header helpers, Level 2 cutout and wavelength reader
processing/   output grid and resampling, background, epochs, co-adds
photometry    forced aperture photometry
catalogs/     Gaia DR3, CatWISE2020 and 2MASS cone searches behind one cone()
pipeline/     sequence parameters, per-frame work, assembly, the result format, background jobs
services      the facade the HTTP API and the MCP server call
"""

__version__ = "0.1.0"
