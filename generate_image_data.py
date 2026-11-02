from pathlib import Path
import math

import numpy as np
import pandas as pd
from PIL import Image


# ----------------------------------------
# SETTINGS
# ----------------------------------------

BASE_DIR = Path(__file__).resolve().parent

IMAGE_PATH = BASE_DIR / "images" / "example_image.jpg"
DMC_LOOKUP_PATH = BASE_DIR / "data" / "dmc_threads.csv"
OUTPUT_PATH = BASE_DIR / "data" / "image_pixels_with_dmc_scaffold.csv"

MAX_PIXELS = 60_000


# ---------------------------------------
# FUNCTIONS
# ---------------------------------------

def resize_to_max_pixels(image, max_pixels):
    """
    Resize an image while preserving its aspect ratio.

    The resized image will have no more than max_pixels total pixels.
    """
    original_width, original_height = image.size
    original_pixel_count = original_width * original_height

    # Keep smaller images at their original resolution
    if original_pixel_count <= max_pixels:
        return image, original_width, original_height

    scale = math.sqrt(max_pixels / original_pixel_count)

    new_width = max(1, math.floor(original_width * scale))
    new_height = max(1, math.floor(original_height * scale))

    # Extra safety check in case rounding nudges us over the limit
    while new_width * new_height > max_pixels:
        if new_width >= new_height:
            new_width -= 1
        else:
            new_height -= 1

    resized_image = image.resize(
        (new_width, new_height),
        Image.Resampling.LANCZOS
    )

    return resized_image, original_width, original_height


def create_pixel_rows(image, original_width, original_height):
    """
    Create one Tableau-ready row per resized image pixel.
    """
    image_width, image_height = image.size
    pixel_array = np.array(image)

    pixel_y, pixel_x = np.indices((image_height, image_width))

    pixel_df = pd.DataFrame({
        "Row Type": "Pixel",

        "Pixel X": pixel_x.ravel(),
        "Pixel Y": pixel_y.ravel(),

        "R": pixel_array[:, :, 0].ravel(),
        "G": pixel_array[:, :, 1].ravel(),
        "B": pixel_array[:, :, 2].ravel(),

        "Image Width": image_width,
        "Image Height": image_height,

        "Original Image Width": original_width,
        "Original Image Height": original_height,

        # Empty for image rows.
        # These will eventually be populated through Tableau/TabPy logic.
        "Thread ID": pd.NA,
        "Thread Label": pd.NA,
        "DMC ID": pd.NA,
        "DMC Name": pd.NA,
        "DMC Hex": pd.NA,
        "DMC Palette Sort": pd.NA,

        # Keep scaffold RGB separate from actual image RGB.
        "DMC R": pd.NA,
        "DMC G": pd.NA,
        "DMC B": pd.NA,
    })

    return pixel_df


def create_scaffold_rows(dmc_df, image_width, image_height,
                         original_width, original_height):
    """
    Create one scaffold row per DMC thread.

    The source order is preserved and becomes DMC Palette Sort,
    which should align with the custom palette order in Preferences.tps.
    """
    scaffold_df = pd.DataFrame({
        "Row Type": "Scaffold",

        "Pixel X": pd.NA,
        "Pixel Y": pd.NA,

        # Blank because these are not image pixels.
        "R": pd.NA,
        "G": pd.NA,
        "B": pd.NA,

        "Image Width": image_width,
        "Image Height": image_height,

        "Original Image Width": original_width,
        "Original Image Height": original_height,

        "Thread ID": dmc_df["Thread ID"],
        "Thread Label": dmc_df["Thread Label"],
        "DMC ID": dmc_df["DMC ID"],
        "DMC Name": dmc_df["DMC Name"],
        "DMC Hex": dmc_df["Hex"],

        # This preserves the current order of your DMC file.
        "DMC Palette Sort": range(1, len(dmc_df) + 1),

        "DMC R": dmc_df["R"],
        "DMC G": dmc_df["G"],
        "DMC B": dmc_df["B"],
    })

    return scaffold_df


def create_tableau_image_data(
    image_path,
    dmc_lookup_path,
    output_path,
    max_pixels=60_000
):
    """
    Creates a combined Tableau input CSV containing:
      - pixel rows from a resized image
      - scaffold rows for every DMC thread
    """

    # Load DMC lookup
    dmc_df = pd.read_csv(
    dmc_lookup_path,
    dtype={"DMC ID": str}
)

    required_columns = [
        "Thread ID",
        "Thread Label",
        "DMC ID",
        "DMC Name",
        "R",
        "G",
        "B",
        "Hex"
    ]

    missing_columns = [
        column for column in required_columns
        if column not in dmc_df.columns
    ]

    if missing_columns:
        raise ValueError(
            "Missing required DMC columns: "
            + ", ".join(missing_columns)
        )

    # Load and resize image
    image = Image.open(image_path).convert("RGB")

    resized_image, original_width, original_height = resize_to_max_pixels(
        image=image,
        max_pixels=max_pixels
    )

    image_width, image_height = resized_image.size

    # Build both row types
    pixel_df = create_pixel_rows(
        image=resized_image,
        original_width=original_width,
        original_height=original_height
    )

    scaffold_df = create_scaffold_rows(
        dmc_df=dmc_df,
        image_width=image_width,
        image_height=image_height,
        original_width=original_width,
        original_height=original_height
    )

    # Union the rows together
    output_df = pd.concat(
        [pixel_df, scaffold_df],
        ignore_index=True
    )

    output_df.to_csv(output_path, index=False)

    print(f"Created: {output_path}")
    print(f"Resized image: {image_width} × {image_height}")
    print(f"Pixel rows: {len(pixel_df):,}")
    print(f"Scaffold rows: {len(scaffold_df):,}")
    print(f"Total rows: {len(output_df):,}")

    return output_df


# --------------------------------------
# RUN
# --------------------------------------

result = create_tableau_image_data(
    image_path=IMAGE_PATH,
    dmc_lookup_path=DMC_LOOKUP_PATH,
    output_path=OUTPUT_PATH,
    max_pixels=MAX_PIXELS
)