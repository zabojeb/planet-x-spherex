"""'Analyze this field': automated searches over every source of a built sequence.

    sources       detection on the co-adds, merged across epochs and detectors, per-detector fluxes
    crossmatch    Gaia DR3 / CatWISE2020 / 2MASS counterparts at the sequence's mean epoch
    movers        motion fits for every bright source; shift-and-stack for faint movers
    transients    intra-visit moving objects: difference images, tracklets, JPL known-object check, ADES export
    variability   light curves of every source, chi^2 / Stetson J / amplitude, periodograms
    colours       red-excess (cold brown dwarf) search and an isolation-forest anomaly score
    realbogus     features and a boosted-stump classifier for difference-image detections
    analyze       the orchestration (FieldOptions, analyze_field)
    jobs          deduplicated, cancellable jobs; results stored next to the sequence
"""

from .analyze import FIELD_VERSION, FieldOptions, analyze_field
from .jobs import field_jobs, job_id, read_result, result_path

__all__ = ["FIELD_VERSION", "FieldOptions", "analyze_field", "field_jobs", "job_id", "read_result", "result_path"]
