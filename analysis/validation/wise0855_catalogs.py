"""WISE 0855-0714: positions from AllWISE, CatWISE2020 (+reject) and Gaia DR3 around the challenge position.

Run on the dev server:  python wise0855_catalogs.py
"""
import csv
import io
import urllib.parse
import urllib.request

IRSA = "https://irsa.ipac.caltech.edu/TAP/sync"
GAIA = "https://gea.esac.esa.int/tap-server/tap/sync"
RA0, DEC0 = 133.795129, -7.245147  # challenge brief (WISE designation epoch ~2010.5)


def tap(url, q):
    full = url + "?" + urllib.parse.urlencode({"QUERY": q, "FORMAT": "csv", "REQUEST": "doQuery", "LANG": "ADQL"})
    with urllib.request.urlopen(full, timeout=300) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode())))


def circle(r):
    return f"1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {RA0}, {DEC0}, {r}))"


print("== AllWISE (red sources, W1-W2 > 2 or W1 undetected, W2 < 15) within 4'")
for x in tap(IRSA, f"""SELECT designation, ra, dec, w1mpro, w1sigmpro, w2mpro, w2sigmpro, pmra, pmdec, sigpmra, sigpmdec,
        w1mjdmean, w2mjdmean, ph_qual FROM allwise_p3as_psd WHERE {circle(4/60)} AND w2mpro < 15
        AND (w1sigmpro IS NULL OR w1mpro - w2mpro > 2)"""):
    print(x)

for table in ("catwise_2020", "catwise_2020_reject"):
    print(f"== {table}: red sources within 4' (W1-W2 > 2)")
    try:
        for x in tap(IRSA, f"""SELECT source_name, ra, dec, w1mpro, w2mpro, pmra, pmdec, sigpmra, sigpmdec, meanobsmjd
                FROM {table} WHERE {circle(4/60)} AND w2mpro < 15.5 AND w1mpro - w2mpro > 2"""):
            print(x)
    except Exception as e:  # noqa: BLE001
        print("  query failed:", e)

print("== Gaia DR3 within 20\" of the position predicted for 2025.9 with the preset motion")
ra_p, dec_p = 133.76013, -7.24226
for x in tap(GAIA, f"""SELECT source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, bp_rp FROM gaiadr3.gaia_source
        WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra_p}, {dec_p}, {20/3600}))"""):
    print(x)
