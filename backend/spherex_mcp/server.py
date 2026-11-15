"""The SPHEREx discovery MCP server: tools, a resource and a discovery prompt over spherex_core.services.

Works with the official `mcp` Python SDK v2 (MCPServer) and v1 (FastMCP).
"""

from __future__ import annotations

import json

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError as _SdkToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError as _SdkToolError  # type: ignore[no-redef]

from spherex_core import __version__

from . import tools as t
from .tools import ToolError

INSTRUCTIONS = """\
You control a NASA SPHEREx time-domain viewer. SPHEREx maps the whole sky every ~6 months in 102 near-infrared
channels (0.75-5 um, detectors D1..D6). For any position this server finds every exposure, aligns them, co-adds
each detector per visit (epoch) and measures sources. You can look at the images and run automated searches.

Typical discovery loop: pick fields (list_presets, resolve_target, or your own RA/Dec) -> search_observations ->
build_sequence + poll sequence_status -> describe_sequence -> get_blink_strip / get_image(mode="difference")
-> analyze_field + poll job_result -> verify each candidate with analyze_point, get_image, catalog_cone and
known_objects -> report with evidence and a confidence level. Pixels are 6.2"; motions below ~1.2"/yr are not
significant with 3-4 visits. Saturated bright stars only have relative photometry. Comets and asteroids:
moving_object_search -> build_object_sequence (static_sky) -> clean_stack / comet_analysis / asteroid_lightcurve.
Be economical: builds take 20-60 s and are capped per hour; reuse sequences you already built."""


def _err(fn):
    """Turn ToolError, lookup and value errors into the SDK error type, whose message reaches the agent."""
    import functools
    import inspect

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **k):
            try:
                return await fn(*a, **k)
            except (ToolError, LookupError, ValueError) as e:
                raise _SdkToolError(str(e)) from None
    else:
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except (ToolError, LookupError, ValueError) as e:
                raise _SdkToolError(str(e)) from None
    return wrapper


def build_server() -> _Server:
    mcp = _Server(name="spherex-discovery", instructions=INSTRUCTIONS)

    # ------------------------------------------------------------ targets and observations
    @mcp.tool()
    @_err
    def list_presets() -> list[dict]:
        """The five challenge targets (Barnard's Star, eps Indi B, WISE 0855, RW Aur, R Dra) with coordinates,
        proper motion and a suggested field of view. Good for learning what known movers and variables look like."""
        return t.list_presets()

    @mcp.tool()
    @_err
    def resolve_target(query: str) -> dict:
        """Object name (e.g. "Barnard's star", "TRAPPIST-1") or coordinates ("269.45 4.69", "17:57:48 +04:41:36")
        -> {ra, dec, pmra, pmdec (mas/yr), ref_epoch}. Pass the proper motion to build_sequence for fast movers."""
        return t.resolve_target(query)

    @mcp.tool()
    @_err
    async def search_observations(ra: float, dec: float) -> dict:
        """How many SPHEREx exposures cover (ra, dec) degrees, per detector and per release, grouped into visits.
        Fields with 3+ visits are best for proper motion; near the ecliptic poles there are hundreds of exposures."""
        return await t.search_observations(ra, dec)

    # ------------------------------------------------------------ sequences
    @mcp.tool()
    @_err
    async def build_sequence(ra: float, dec: float, pmra: float = 0.0, pmdec: float = 0.0, ref_epoch: float = 2000.0,
                             fov_arcmin: float = 6.0, name: str | None = None) -> dict:
        """Start building the time sequence for a field (all exposures aligned, co-added per visit and detector,
        target photometry). Returns sequence_id at once; poll sequence_status. fov_arcmin 1-20 (6 is a good
        default; larger fields find more movers but take longer). Already-built fields return status "done"."""
        return await t.build_sequence(ra, dec, pmra, pmdec, ref_epoch, fov_arcmin, name)

    @mcp.tool()
    @_err
    async def sequence_status(sequence_id: str) -> dict:
        """Progress of a build: queued / running (stage, done/total exposures) / done / error."""
        return await t.sequence_status(sequence_id)

    @mcp.tool()
    @_err
    async def cancel_job(job_id: str) -> dict:
        """Cancel a build (sequence_id) or analysis (job_id) you no longer need, freeing the worker."""
        return await t.cancel_job(job_id)

    @mcp.tool()
    @_err
    def describe_sequence(sequence_id: str) -> dict:
        """Summary of a built sequence: target, grid, epochs with dates and frames per detector, and the
        target's photometry per detector."""
        return t.describe_sequence(sequence_id)

    # ------------------------------------------------------------ images
    @mcp.tool()
    @_err
    def get_image(sequence_id: str, epoch: int = -1, band: str = "ALL", mode: str = "coadd", reference: int = 0,
                  stretch: str = "asinh") -> list:
        """Render one epoch as a PNG you can look at. band: D1..D6 or ALL (noise-weighted sum). mode: "coadd",
        "difference" (epoch minus `reference`: a mover appears as a red/blue pair, a variable as a single red or
        blue blob) or "rgb" (false colour; cold brown dwarfs are red). epoch -1 = last. North up, east left."""
        png, info = t.get_image(sequence_id, epoch, band, mode, reference, stretch)
        return [json.dumps(info), Image(data=png, format="png")]

    @mcp.tool()
    @_err
    def get_blink_strip(sequence_id: str, band: str = "ALL", stretch: str = "asinh") -> list:
        """All epochs side by side in one PNG on the same brightness scale: the quickest way to spot something
        that moves or changes. Amber crosshair = predicted target position per epoch."""
        png, info = t.get_blink_strip(sequence_id, band, stretch)
        return [json.dumps(info), Image(data=png, format="png")]

    # ------------------------------------------------------------ analysis
    @mcp.tool()
    @_err
    async def analyze_point(sequence_id: str, ra: float, dec: float, pmra: float = 0.0, pmdec: float = 0.0,
                            ref_epoch: float = 2016.0) -> dict:
        """Full automatic analysis of one position in a built sequence: forced photometry in every exposure,
        spectrum features and classification (star / M / L / T dwarf / YSO ...), spectral variability, proper
        motion from the co-adds, periodogram, and nearby Gaia sources. Use it to verify a candidate."""
        return await t.analyze_point(sequence_id, ra, dec, pmra, pmdec, ref_epoch)

    @mcp.tool()
    @_err
    async def spectrum_classify(sequence_id: str) -> dict:
        """Template classification of the sequence target's SPHEREx spectrum with catalog magnitude checks."""
        return await t.spectrum_classify(sequence_id)

    @mcp.tool()
    @_err
    async def analyze_field(sequence_id: str, snr_min: float = 5.0) -> dict:
        """Start "analyze this field": every source is detected, matched to Gaia/CatWISE/2MASS, fitted for
        proper motion (movers, especially ones missing from Gaia or disagreeing with it), searched for faint
        movers by shift-and-stack, for asteroids/comets inside each visit (tracklets with real/bogus scores and
        known-object identification), for variables, and for red 4-5 um excess (cold brown dwarf candidates).
        Returns job_id; poll job_result (1-5 min)."""
        return await t.analyze_field(sequence_id, snr_min)

    @mcp.tool()
    @_err
    async def job_result(job_id: str, limit: int = 12) -> dict:
        """Status of an analysis job (analyze_field, known_objects, track_and_stack_search, comet_analysis,
        asteroid_lightcurve, unknown_movers); when done, a compact summary: candidate lists with evidence for
        field jobs, Afrho / gas bands / activity for comets, period / taxonomy for asteroids."""
        return await t.job_result(job_id, limit)

    @mcp.tool()
    @_err
    async def known_objects(sequence_id: str, vmag_lim: float = 21.0) -> dict:
        """Start a lookup of known asteroids and comets in the field at each epoch, positions computed from
        SPHEREx's own location (JPL). Returns job_id; poll job_result. Use it before claiming a new object."""
        return await t.known_objects(sequence_id, vmag_lim)

    @mcp.tool()
    @_err
    async def track_and_stack_search(sequence_id: str, epoch: int, rate_min: float = 0.3,
                                     rate_max: float = 10.0) -> dict:
        """Start a synthetic-tracking search inside one visit over trial motions rate_min..rate_max arcsec/hour
        for faint asteroids, comets or a slow distant planet. Returns job_id; poll job_result."""
        return await t.track_and_stack_search(sequence_id, epoch, rate_min, rate_max)

    @mcp.tool()
    @_err
    def track_and_stack_image(sequence_id: str, epoch: int, rate_arcsec_per_hour: float, pa_deg: float,
                              subtract_static: bool = True) -> list:
        """Stack one visit's exposures along a motion (rate, position angle east of north) and show the PNG.
        A real object at that motion becomes a sharp point; use it to confirm a tracklet."""
        png, info = t.track_and_stack_image(sequence_id, epoch, rate_arcsec_per_hour, pa_deg, subtract_static)
        return [json.dumps(info), Image(data=png, format="png")]

    @mcp.tool()
    @_err
    async def moving_object_search(name: str) -> dict:
        """Which SPHEREx exposures saw a known asteroid or comet (IRSA MOST), e.g. "3I/ATLAS" or "433 Eros".
        The result has a suggested_request: pass its ra/dec to build_sequence with object fields via the API,
        or study the dates. May return status "running" first: call again."""
        return await t.moving_object_search(name)

    @mcp.tool()
    @_err
    async def archival_epochs(sequence_id: str) -> dict:
        """2MASS (2000), AllWISE (2010) and NEOWISE (2014-2024) images on the same grid: a 26-year baseline for
        slow movers. Starts the build on first call (1-2 min); call again until status is "done"."""
        return await t.archival_epochs(sequence_id)

    # ------------------------------------------------------------ comets and asteroids (ADR-0057)
    @mcp.tool()
    @_err
    async def build_object_sequence(name: str, fov_arcmin: float = 8.0, static_sky: bool = True,
                                    date_min: str | None = None, date_max: str | None = None) -> dict:
        """Build a co-moving sequence of a known asteroid or comet ("3I/ATLAS", "433 Eros"): every exposure is
        centred on the object, so it stays still while stars stream past. static_sky=True also subtracts the
        sky behind the track using other SPHEREx visits. date_min/date_max (YYYY-MM-DD) narrow the window. Poll
        sequence_status; then comet_analysis / asteroid_lightcurve / clean_stack."""
        return await t.build_object_sequence(name, fov_arcmin, static_sky, date_min, date_max)

    @mcp.tool()
    @_err
    async def clean_stack(sequence_id: str, epoch: int = -1, band: str = "ALL", stretch: str = "asinh") -> list:
        """The static-sky subtracted co-add of one epoch of a co-moving sequence: the comet or asteroid on an
        empty background (D5 shows a comet's CO2 coma). Reports the noise and object S/N clean vs plain. On a
        sequence without static sky it starts that build and returns the new sequence id to poll."""
        info, png = await t.clean_stack(sequence_id, epoch, band, stretch)
        return [json.dumps(info)] + ([Image(data=png, format="png")] if png else [])

    @mcp.tool()
    @_err
    async def comet_analysis(sequence_id: str, rho_km: list[float] | None = None) -> dict:
        """Start a comet analysis of a co-moving sequence: photometry in apertures of fixed size in km, coma
        radial profile, Afrho and A(0)frho, CO2 4.26 / CO 4.67 / H2O 2.7 um gas bands with significance and
        production-rate proxies (or 3-sigma upper limits), water-ice 1.5/2/3 um depths, dust colour, activity vs
        heliocentric distance. Returns job_id; poll job_result."""
        return await t.comet_analysis(sequence_id, rho_km)

    @mcp.tool()
    @_err
    async def asteroid_lightcurve(sequence_id: str, min_period_h: float = 1.5,
                                  max_period_h: float | None = None) -> dict:
        """Start an asteroid analysis of a co-moving sequence: light-time and distance corrected reduced
        magnitudes, a multi-detector Fourier periodogram (rotation period with error, double-peak test),
        reflectance spectrum, 1/2/3 um band depths, spectral slope, thermal tail and a rough S/C/X/D/V
        taxonomy with caveats. Returns job_id; poll job_result."""
        return await t.asteroid_lightcurve(sequence_id, min_period_h, max_period_h)

    @mcp.tool()
    @_err
    async def unknown_movers(sequence_id: str, vmag_lim: float = 21.0, field_job: str | None = None) -> dict:
        """Start a search for movers no known object explains in a fixed-position sequence: tracklets with
        rate, PA, motion vs the ecliptic, detector-pair confirmation, real/bogus score and a population hint.
        field_job reuses a finished analyze_field job. Export with export_report(fmt="ades", job_id=...)."""
        return await t.unknown_movers(sequence_id, vmag_lim, field_job)

    @mcp.tool()
    @_err
    def catalog_cone(catalog: str, ra: float, dec: float, radius_arcmin: float = 2.0, limit: int = 25) -> dict:
        """Sources from "gaia" (DR3, positions at 2016.0, proper motions), "catwise" (2015.4, motions),
        "2mass" (~1999) or "simbad" around a position; also lists high-proper-motion sources (>100 mas/yr)."""
        return t.catalog_cone(catalog, ra, dec, radius_arcmin, limit)

    @mcp.tool()
    @_err
    def export_report(sequence_id: str, fmt: str, job_id: str | None = None) -> dict:
        """Export results: "csv" or "votable" (target photometry), "ades" (moving-object astrometry from an
        analyze_field job; follow-up data, not an official MPC submission), "fits" (download link for co-adds)."""
        return t.export_report(sequence_id, fmt, job_id)

    # ------------------------------------------------------------ resource and prompt
    @mcp.resource("spherex://overview", name="overview", mime_type="text/markdown",
                  description="What the data are, the presets, and how to use the tools.")
    def overview() -> str:
        presets = "\n".join(f"- {p['name']}: {p['description']} (ra {p['ra']:.4f}, dec {p['dec']:.4f})"
                            for p in t.list_presets())
        return f"# SPHEREx discovery server {__version__}\n\n{INSTRUCTIONS}\n\n## Presets\n{presets}\n"

    @mcp.prompt(name="discovery_report", description="Guide an agent through a search for new objects.")
    def discovery_report(fields: str = "the five presets plus two random high-galactic-latitude fields",
                         goal: str = "brown dwarfs or other high-proper-motion stars and moving solar-system objects"
                         ) -> str:
        return f"""Search SPHEREx for {goal} in {fields}.

For each field:
1. search_observations to confirm 3+ visits; build_sequence (fov 6-12'), poll sequence_status.
2. get_blink_strip with band ALL and D6; describe what changes.
3. analyze_field, poll job_result. Take the top movers, faint_movers, tracklets, red and variable candidates.
4. For each candidate: analyze_point (motion, spectrum class, variability), get_image mode="difference",
   catalog_cone gaia/catwise at its position, and known_objects for tracklets.
5. Call something "new" only if it is significant (S/N, motion error, several epochs or detections), real (not
   flagged, consistent in paired detectors), and absent or discrepant in Gaia / CatWISE / known-object lists.

Finish with a report: a table of candidates (field, RA/Dec, type, measured motion or rate, key evidence,
catalog status, confidence high/medium/low), what you checked, and what follow-up would confirm each one."""

    return mcp
