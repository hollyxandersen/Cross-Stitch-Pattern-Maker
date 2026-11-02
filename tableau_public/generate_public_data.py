"""Generate precomputed cross-stitch data for a Tableau Public workbook.

Designed to run in Google Colab. The script asks for exactly three images and
then a DMC palette CSV/XLSX. It creates one pixel-level dataset containing four
stitch-size versions of each image. Four independently fitted K-means/DMC
results are stored as parallel field groups, one for each maximum-colour choice.

Output structure
----------------
* One row per rendered pixel for each Image x Stitch Setting.
* Rendered longest edge is always 200 pixels, so every version occupies the
  same visual area while lower-resolution stitches appear as blocks.
* DMC scaffold rows are appended for every Image x Stitch Setting x DMC colour.
  They have Row Type = "DMC Scaffold" and Stitch Weight = 0.
"""

from __future__ import annotations

import io
import re
import sys
import subprocess
import zipfile
from pathlib import Path


def _install_if_missing(import_name: str, package_name: str | None = None) -> None:
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", package_name or import_name]
        )


for _import, _package in [
    ("numpy", None),
    ("pandas", None),
    ("PIL", "Pillow"),
    ("sklearn", "scikit-learn"),
    ("openpyxl", None),
]:
    _install_if_missing(_import, _package)

import numpy as np
import pandas as pd
from PIL import Image, ImageOps
from sklearn.cluster import MiniBatchKMeans


# ---------------------------------------------------------------------------
# Fixed choices used by the Tableau Public workbook
# ---------------------------------------------------------------------------

STITCH_OPTIONS = (50, 100, 160, 200)
COLOUR_OPTIONS = (10, 25, 40, 60)
DISPLAY_LONGEST_EDGE = 200
RANDOM_STATE = 42

OUTPUT_CSV = "tableau_public_cross_stitch_pixels.csv"
OUTPUT_PALETTE = "dmc_palette_clean.csv"
OUTPUT_SUMMARY = "cross_stitch_generation_summary.csv"
OUTPUT_ZIP = "tableau_public_cross_stitch_data.zip"


def upload_files(prompt: str) -> dict[str, bytes]:
    """Use Colab's upload widget and return {filename: bytes}."""
    print("\n" + prompt)
    try:
        from google.colab import files
    except ImportError as exc:
        raise RuntimeError(
            "This script is designed for Google Colab. Open it in Colab and run all cells."
        ) from exc
    return dict(files.upload())


def upload_three_images() -> list[tuple[str, Image.Image]]:
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    while True:
        uploaded = upload_files(
            "Upload exactly THREE source images (PNG, JPG, JPEG, WEBP, BMP or TIFF)."
        )
        valid = [(name, data) for name, data in uploaded.items() if Path(name).suffix.lower() in allowed]
        if len(valid) != 3:
            print(f"I found {len(valid)} valid image files. Please try again with exactly three.")
            continue

        images: list[tuple[str, Image.Image]] = []
        try:
            for filename, raw in valid:
                image = Image.open(io.BytesIO(raw))
                image = ImageOps.exif_transpose(image).convert("RGB")
                images.append((filename, image))
        except Exception as exc:
            print(f"One of the files could not be opened as an image ({exc}). Please try again.")
            continue

        print("\nImages loaded:")
        for index, (filename, image) in enumerate(images, start=1):
            print(f"  {index}. {filename} — {image.width} × {image.height}")
        return images


def upload_dmc_palette() -> tuple[str, bytes]:
    allowed = {".csv", ".xlsx", ".xls"}
    while True:
        uploaded = upload_files(
            "Upload the DMC palette file (CSV or Excel) containing number, name, red, green and blue."
        )
        valid = [(name, data) for name, data in uploaded.items() if Path(name).suffix.lower() in allowed]
        if len(valid) != 1:
            print(f"I found {len(valid)} valid palette files. Please upload exactly one.")
            continue
        return valid[0]


def normalise_column_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def find_column(columns: list[str], candidates: tuple[str, ...], label: str) -> str:
    normalised = {normalise_column_name(column): column for column in columns}
    for candidate in candidates:
        if candidate in normalised:
            return normalised[candidate]
    raise ValueError(
        f"Could not identify the {label} column. Columns found: {', '.join(map(str, columns))}"
    )


def find_optional_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    normalised = {normalise_column_name(column): column for column in columns}
    for candidate in candidates:
        if candidate in normalised:
            return normalised[candidate]
    return None


def load_dmc_palette(filename: str, raw: bytes) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            frame = pd.read_csv(io.BytesIO(raw))
        except UnicodeDecodeError:
            frame = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
    else:
        frame = pd.read_excel(io.BytesIO(raw))

    columns = list(frame.columns)
    code_col = find_column(
        columns,
        ("dmcid", "dmcnumber", "dmcno", "dmccode", "dmc", "flossnumber", "number", "code"),
        "DMC number",
    )
    name_col = find_column(
        columns,
        ("dmcname", "flossname", "threadname", "colorname", "colourname", "name", "description"),
        "DMC name",
    )
    red_col = find_column(columns, ("dmcr", "red", "r", "rgbred"), "red")
    green_col = find_column(columns, ("dmcg", "green", "g", "rgbgreen"), "green")
    blue_col = find_column(columns, ("dmcb", "blue", "b", "rgbblue"), "blue")
    thread_id_col = find_optional_column(columns, ("threadid",))
    thread_label_col = find_optional_column(columns, ("threadlabel",))
    hex_col = find_optional_column(columns, ("dmchex", "hex", "hexcode", "colourhex", "colorhex"))
    palette_sort_col = find_optional_column(
        columns, ("dmcpalettesort", "palettesort", "sortorder", "sort")
    )

    palette = pd.DataFrame(
        {
            "Thread ID": frame[thread_id_col] if thread_id_col else np.arange(1, len(frame) + 1),
            "Thread Label": frame[thread_label_col] if thread_label_col else "",
            "DMC ID": frame[code_col],
            "DMC Name": frame[name_col],
            "DMC Hex": frame[hex_col] if hex_col else "",
            "DMC Palette Sort": (
                frame[palette_sort_col] if palette_sort_col else np.arange(1, len(frame) + 1)
            ),
            "DMC R": frame[red_col],
            "DMC G": frame[green_col],
            "DMC B": frame[blue_col],
        }
    )
    palette = palette.dropna(subset=["DMC ID", "DMC R", "DMC G", "DMC B"])
    palette["DMC ID"] = palette["DMC ID"].astype(str).str.strip()
    palette["DMC Name"] = palette["DMC Name"].fillna("").astype(str).str.strip()
    palette["Thread Label"] = palette["Thread Label"].fillna("").astype(str).str.strip()
    palette["Thread ID"] = pd.to_numeric(palette["Thread ID"], errors="coerce").astype("Int64")
    palette["DMC Palette Sort"] = pd.to_numeric(
        palette["DMC Palette Sort"], errors="coerce"
    ).astype("Int64")
    for column in ["DMC R", "DMC G", "DMC B"]:
        palette[column] = pd.to_numeric(palette[column], errors="coerce")
    palette = palette.dropna(subset=["DMC R", "DMC G", "DMC B"])
    palette[["DMC R", "DMC G", "DMC B"]] = (
        palette[["DMC R", "DMC G", "DMC B"]].round().clip(0, 255).astype("uint8")
    )
    palette = palette.drop_duplicates(subset=["DMC ID"], keep="first").reset_index(drop=True)
    calculated_hex = (
        "#"
        + palette["DMC R"].map("{:02X}".format)
        + palette["DMC G"].map("{:02X}".format)
        + palette["DMC B"].map("{:02X}".format)
    )
    supplied_hex = palette["DMC Hex"].fillna("").astype(str).str.strip()
    supplied_hex = supplied_hex.where(supplied_hex.str.match(r"^#[0-9A-Fa-f]{6}$"), calculated_hex)
    palette["DMC Hex"] = supplied_hex.str.upper()

    # Fill optional scaffold fields if an otherwise valid palette omitted them.
    missing_thread_ids = palette["Thread ID"].isna()
    palette.loc[missing_thread_ids, "Thread ID"] = np.arange(1, len(palette) + 1)[missing_thread_ids]
    missing_sorts = palette["DMC Palette Sort"].isna()
    palette.loc[missing_sorts, "DMC Palette Sort"] = np.arange(1, len(palette) + 1)[missing_sorts]
    missing_labels = palette["Thread Label"].eq("")
    palette.loc[missing_labels, "Thread Label"] = (
        palette.loc[missing_labels, "Thread ID"].astype(str).str.zfill(3)
        + " - "
        + palette.loc[missing_labels, "DMC ID"]
    )
    if palette.empty:
        raise ValueError("The DMC palette did not contain any valid RGB rows.")
    print(f"DMC palette loaded: {len(palette):,} colours")
    return palette


def safe_image_name(filename: str, existing: set[str]) -> str:
    base = re.sub(r"[_-]+", " ", Path(filename).stem).strip()
    base = re.sub(r"\s+", " ", base) or "Image"
    candidate = base
    suffix = 2
    while candidate.casefold() in existing:
        candidate = f"{base} {suffix}"
        suffix += 1
    existing.add(candidate.casefold())
    return candidate


def dimensions_for_longest_edge(width: int, height: int, longest_edge: int) -> tuple[int, int]:
    if width >= height:
        return longest_edge, max(1, round(height * longest_edge / width))
    return max(1, round(width * longest_edge / height)), longest_edge


def resize_rgb(image: Image.Image, width: int, height: int) -> np.ndarray:
    return np.asarray(image.resize((width, height), Image.Resampling.LANCZOS), dtype=np.uint8)


def fit_kmeans_and_match_dmc(
    stitch_rgb: np.ndarray,
    requested_colours: int,
    palette: pd.DataFrame,
) -> tuple[np.ndarray, int]:
    """Cluster one pattern version independently, then map centroids to nearest DMC."""
    pixels = stitch_rgb.reshape(-1, 3).astype(np.float32)
    unique_count = len(np.unique(pixels.astype(np.uint8), axis=0))
    actual_colours = max(1, min(requested_colours, unique_count, len(pixels)))

    model = MiniBatchKMeans(
        n_clusters=actual_colours,
        random_state=RANDOM_STATE,
        n_init=10,
        batch_size=min(4096, max(256, len(pixels))),
        reassignment_ratio=0.0,
    )
    labels = model.fit_predict(pixels)
    centres = model.cluster_centers_.astype(np.float32)
    dmc_rgb = palette[["DMC R", "DMC G", "DMC B"]].to_numpy(dtype=np.float32)

    # Squared Euclidean RGB distance, matching the original Tableau/TabPy approach.
    distances = ((centres[:, None, :] - dmc_rgb[None, :, :]) ** 2).sum(axis=2)
    centre_to_dmc = distances.argmin(axis=1)
    dmc_indices = centre_to_dmc[labels].reshape(stitch_rgb.shape[:2])
    return dmc_indices, actual_colours


def expand_stitches_to_display(
    stitch_values: np.ndarray,
    display_width: int,
    display_height: int,
) -> np.ndarray:
    """Nearest-neighbour expansion keeps each rendered pixel assigned to one stitch."""
    image = Image.fromarray(stitch_values.astype(np.int32), mode="I")
    return np.asarray(
        image.resize((display_width, display_height), Image.Resampling.NEAREST),
        dtype=np.int32,
    )


def build_pixel_version(
    image_id: int,
    image_name: str,
    filename: str,
    image: Image.Image,
    stitch_longest_edge: int,
    palette: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    stitch_width, stitch_height = dimensions_for_longest_edge(
        image.width, image.height, stitch_longest_edge
    )
    display_width, display_height = dimensions_for_longest_edge(
        image.width, image.height, DISPLAY_LONGEST_EDGE
    )
    stitch_rgb = resize_rgb(image, stitch_width, stitch_height)

    pixel_x = np.tile(np.arange(1, display_width + 1, dtype=np.int16), display_height)
    # Y is reversed here so increasing Tableau Y plots the image the right way up.
    pixel_y = np.repeat(np.arange(display_height, 0, -1, dtype=np.int16), display_width)
    source_rgb = resize_rgb(image, display_width, display_height).reshape(-1, 3)

    # Proportional binning handles settings such as 160 that do not divide 200 evenly.
    source_x = np.tile(np.arange(display_width), display_height)
    source_y_top = np.repeat(np.arange(display_height), display_width)
    stitch_x = np.floor(source_x * stitch_width / display_width).astype(np.int16) + 1
    stitch_y_top = np.floor(source_y_top * stitch_height / display_height).astype(np.int16)
    stitch_y = (stitch_height - stitch_y_top).astype(np.int16)

    orientation = "Landscape" if image.width > image.height else "Portrait" if image.height > image.width else "Square"
    frame = pd.DataFrame(
        {
            "Row Type": "Pixel",
            "Image ID": image_id,
            "Image Name": image_name,
            "Source Filename": filename,
            "Orientation": orientation,
            "Original Width": image.width,
            "Original Height": image.height,
            "Stitches Along Longest Edge": stitch_longest_edge,
            "Pattern Width (Stitches)": stitch_width,
            "Pattern Height (Stitches)": stitch_height,
            "Display Width (Pixels)": display_width,
            "Display Height (Pixels)": display_height,
            "Pixel X": pixel_x,
            "Pixel Y": pixel_y,
            "Stitch X": stitch_x,
            "Stitch Y": stitch_y,
            "Stitch ID": [f"{x}-{y}" for x, y in zip(stitch_x, stitch_y)],
            "Stitch Weight": 1.0 / (
                np.bincount(
                    stitch_y_top.astype(np.int64) * stitch_width + (stitch_x - 1).astype(np.int64),
                    minlength=stitch_width * stitch_height,
                )[
                    stitch_y_top.astype(np.int64) * stitch_width + (stitch_x - 1).astype(np.int64)
                ]
            ),
            "Source R": source_rgb[:, 0],
            "Source G": source_rgb[:, 1],
            "Source B": source_rgb[:, 2],
        }
    )

    summaries: list[dict[str, object]] = []
    for colour_limit in COLOUR_OPTIONS:
        dmc_indices, fitted_clusters = fit_kmeans_and_match_dmc(
            stitch_rgb, colour_limit, palette
        )
        expanded = expand_stitches_to_display(dmc_indices, display_width, display_height)
        selected = palette.iloc[expanded.reshape(-1)].reset_index(drop=True)
        prefix = f"{colour_limit} Colours"
        frame[f"Thread ID - {prefix}"] = selected["Thread ID"].to_numpy()
        frame[f"Thread Label - {prefix}"] = selected["Thread Label"].to_numpy()
        frame[f"DMC ID - {prefix}"] = selected["DMC ID"].to_numpy()
        frame[f"DMC Name - {prefix}"] = selected["DMC Name"].to_numpy()
        frame[f"DMC Hex - {prefix}"] = selected["DMC Hex"].to_numpy()
        frame[f"DMC Palette Sort - {prefix}"] = selected["DMC Palette Sort"].to_numpy()
        frame[f"DMC R - {prefix}"] = selected["DMC R"].to_numpy()
        frame[f"DMC G - {prefix}"] = selected["DMC G"].to_numpy()
        frame[f"DMC B - {prefix}"] = selected["DMC B"].to_numpy()

        summaries.append(
            {
                "Image ID": image_id,
                "Image Name": image_name,
                "Stitches Along Longest Edge": stitch_longest_edge,
                "Pattern Width": stitch_width,
                "Pattern Height": stitch_height,
                "Maximum Colours": colour_limit,
                "K-means Clusters Fitted": fitted_clusters,
                "Distinct DMC Threads Used": int(len(np.unique(dmc_indices))),
                "True Stitch Count": stitch_width * stitch_height,
                "Rendered Pixel Rows": display_width * display_height,
            }
        )
    return frame, summaries


def build_dmc_scaffold(
    image_id: int,
    image_name: str,
    filename: str,
    image: Image.Image,
    stitch_longest_edge: int,
    palette: pd.DataFrame,
    all_columns: list[str],
) -> pd.DataFrame:
    """Append every DMC value without contributing pixels, stitches or materials."""
    stitch_width, stitch_height = dimensions_for_longest_edge(
        image.width, image.height, stitch_longest_edge
    )
    display_width, display_height = dimensions_for_longest_edge(
        image.width, image.height, DISPLAY_LONGEST_EDGE
    )
    orientation = "Landscape" if image.width > image.height else "Portrait" if image.height > image.width else "Square"
    scaffold = pd.DataFrame(index=np.arange(len(palette)), columns=all_columns)
    scaffold["Row Type"] = "DMC Scaffold"
    scaffold["Image ID"] = image_id
    scaffold["Image Name"] = image_name
    scaffold["Source Filename"] = filename
    scaffold["Orientation"] = orientation
    scaffold["Original Width"] = image.width
    scaffold["Original Height"] = image.height
    scaffold["Stitches Along Longest Edge"] = stitch_longest_edge
    scaffold["Pattern Width (Stitches)"] = stitch_width
    scaffold["Pattern Height (Stitches)"] = stitch_height
    scaffold["Display Width (Pixels)"] = display_width
    scaffold["Display Height (Pixels)"] = display_height
    scaffold["Stitch Weight"] = 0.0

    for colour_limit in COLOUR_OPTIONS:
        prefix = f"{colour_limit} Colours"
        scaffold[f"Thread ID - {prefix}"] = palette["Thread ID"].to_numpy()
        scaffold[f"Thread Label - {prefix}"] = palette["Thread Label"].to_numpy()
        scaffold[f"DMC ID - {prefix}"] = palette["DMC ID"].to_numpy()
        scaffold[f"DMC Name - {prefix}"] = palette["DMC Name"].to_numpy()
        scaffold[f"DMC Hex - {prefix}"] = palette["DMC Hex"].to_numpy()
        scaffold[f"DMC Palette Sort - {prefix}"] = palette["DMC Palette Sort"].to_numpy()
        scaffold[f"DMC R - {prefix}"] = palette["DMC R"].to_numpy()
        scaffold[f"DMC G - {prefix}"] = palette["DMC G"].to_numpy()
        scaffold[f"DMC B - {prefix}"] = palette["DMC B"].to_numpy()
    return scaffold


def validate_output(frame: pd.DataFrame, palette: pd.DataFrame) -> None:
    pixels = frame[frame["Row Type"] == "Pixel"]
    scaffolds = frame[frame["Row Type"] == "DMC Scaffold"]
    expected_scaffolds = 3 * len(STITCH_OPTIONS) * len(palette)
    if len(scaffolds) != expected_scaffolds:
        raise AssertionError(f"Expected {expected_scaffolds:,} scaffold rows; found {len(scaffolds):,}.")
    if not (scaffolds["Stitch Weight"].fillna(-1) == 0).all():
        raise AssertionError("A DMC scaffold row has a non-zero Stitch Weight.")
    if pixels["Stitch Weight"].isna().any():
        raise AssertionError("A pixel row is missing its Stitch Weight.")

    for colour_limit in COLOUR_OPTIONS:
        column = f"DMC ID - {colour_limit} Colours"
        if pixels[column].isna().any():
            raise AssertionError(f"Pixel rows contain missing assignments in {column}.")

    grouped = pixels.groupby(["Image ID", "Stitches Along Longest Edge"], observed=True)
    for (_, _), group in grouped:
        true_stitches = int(group["Pattern Width (Stitches)"].iloc[0]) * int(
            group["Pattern Height (Stitches)"].iloc[0]
        )
        if not np.isclose(group["Stitch Weight"].sum(), true_stitches):
            raise AssertionError("Stitch Weight does not sum to the true stitch count.")
    print("Output validation passed.")


def main() -> None:
    print("TABLEAU PUBLIC CROSS-STITCH DATA GENERATOR")
    print(f"Stitch settings: {list(STITCH_OPTIONS)}")
    print(f"Maximum-colour settings: {list(COLOUR_OPTIONS)}")
    print(f"Rendered longest edge: {DISPLAY_LONGEST_EDGE} pixels")

    uploaded_images = upload_three_images()
    palette_filename, palette_raw = upload_dmc_palette()
    palette = load_dmc_palette(palette_filename, palette_raw)

    image_names_seen: set[str] = set()
    named_images = [
        (index, safe_image_name(filename, image_names_seen), filename, image)
        for index, (filename, image) in enumerate(uploaded_images, start=1)
    ]

    pixel_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []
    total_jobs = len(named_images) * len(STITCH_OPTIONS)
    job = 0
    for image_id, image_name, filename, image in named_images:
        for stitch_setting in STITCH_OPTIONS:
            job += 1
            print(
                f"[{job}/{total_jobs}] {image_name}: {stitch_setting} stitches "
                f"with independently fitted {list(COLOUR_OPTIONS)}-colour versions..."
            )
            frame, summaries = build_pixel_version(
                image_id, image_name, filename, image, stitch_setting, palette
            )
            pixel_frames.append(frame)
            summary_rows.extend(summaries)

    all_columns = list(pixel_frames[0].columns)
    scaffold_frames: list[pd.DataFrame] = []
    for image_id, image_name, filename, image in named_images:
        for stitch_setting in STITCH_OPTIONS:
            scaffold_frames.append(
                build_dmc_scaffold(
                    image_id,
                    image_name,
                    filename,
                    image,
                    stitch_setting,
                    palette,
                    all_columns,
                )
            )

    output = pd.concat(pixel_frames + scaffold_frames, ignore_index=True)
    validate_output(output, palette)

    summary = pd.DataFrame(summary_rows)
    output.to_csv(OUTPUT_CSV, index=False)
    palette.to_csv(OUTPUT_PALETTE, index=False)
    summary.to_csv(OUTPUT_SUMMARY, index=False)

    with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(OUTPUT_CSV)
        archive.write(OUTPUT_PALETTE)
        archive.write(OUTPUT_SUMMARY)

    print("\nFinished!")
    print(f"Pixel rows: {(output['Row Type'] == 'Pixel').sum():,}")
    print(f"DMC scaffold rows: {(output['Row Type'] == 'DMC Scaffold').sum():,}")
    print(f"Total rows: {len(output):,}")
    print(f"Created: {OUTPUT_ZIP}")

    from google.colab import files

    files.download(OUTPUT_ZIP)


if __name__ == "__main__":
    main()