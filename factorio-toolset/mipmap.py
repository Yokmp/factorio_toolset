# -*- coding: utf-8 -*-
# Python 3
#
# Thanks to Deadlock989 for sharing his script
# https://forums.factorio.com/viewtopic.php?f=69&t=93693
#
# You need the Pillow module for this script to work
# python -m pip install --upgrade Pillow
#
# version 1.2.0

from PIL import Image
import argparse
import json
import math
import os
import platform
import sys


finput = "_single/"
foutput = "_multi/"
mipmap_levels = 2
crop_image = False
verbose = False

parser = argparse.ArgumentParser(
    prog=os.path.basename(__file__),
    description="Generate Factorio-style mipmap strips from all PNG icons in _single.",
)
parser.add_argument(
    "levels",
    nargs="?",
    type=int,
    default=mipmap_levels,
    help="mipmap amount, e.g. 'mipmap.py 2' creates original + 1 half-size copy",
)
parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
parser.add_argument("-c", "--crop", action="store_true", help="crop canvas before mipmapping")
parser.add_argument("-i", "--in", dest="input_folder", default=finput, help="input folder")
parser.add_argument("-o", "--out", dest="output_folder", default=foutput, help="output folder")
args = parser.parse_args()

mipmap_levels = args.levels if args.levels > 0 else 1
crop_image = args.crop
verbose = args.verbose
finput = args.input_folder
foutput = args.output_folder

if not os.path.exists(finput):
    print("Path %s doesn't exist. Aborting." % finput)
    sys.exit(-1)
if not os.path.exists(foutput):
    print("Path %s doesn't exist. Aborting." % foutput)
    sys.exit(-1)

if platform.system() == "Windows":
    finput = finput.replace("/", "\\")
    foutput = foutput.replace("/", "\\")
else:
    finput = finput.replace("\\", "/")
    foutput = foutput.replace("\\", "/")


def crop_canvas(old_image, size):
    old_image = old_image.crop(old_image.getbbox())
    old_width, old_height = old_image.size
    if old_width > size or old_height > size:
        largest = old_width if old_width >= old_height else old_height
        old_image = old_image.resize(
            (int(size * old_width / largest), int(size * old_height / largest)),
            Image.LANCZOS,
        )
        old_width, old_height = old_image.size
    x1 = int(math.floor((size - old_width) / 2))
    y1 = int(math.floor((size - old_height) / 2))
    new_image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    new_image.paste(old_image, (x1, y1, x1 + old_width, y1 + old_height))
    return new_image


def create_mipmap(outputf, inputf, levels):
    with Image.open(inputf) as source:
        source = source.convert("RGBA")
        width, height = source.size
        if width != height:
            raise ValueError(
                "%s is %sx%s, but mipmap icons must be square." % (inputf, width, height)
            )
        size = width
        original = crop_canvas(source, size) if crop_image else source.copy()

    mipmap_width = sum(max(1, int(size * (0.5**i))) for i in range(levels))
    mipmap = Image.new("RGBA", (mipmap_width, size), (0, 0, 0, 0))
    offset = 0
    for i in range(levels):
        new_size = max(1, int(size * (0.5**i)))
        copy = original.resize((new_size, new_size), Image.LANCZOS)
        mipmap.paste(copy, box=(offset, 0))
        offset += new_size
    mipmap.save(outputf)
    return mipmap.size


def file_size(size):
    power_labels = {0: "", 1: "K", 2: "M", 3: "G", 4: "T"}
    n = 0
    while size > 1024:
        size /= 1024
        n += 1
    return round(size, 2), power_labels[n] + "B"


with open("info.json", encoding="utf-8") as info:
    js = json.load(info)
    version = js["version"]
    mod_title = js["title"]

if mod_title == "":
    mod_title = "unknown mod"
if version == "":
    version = "?.?.?"

png_files = []
for root, dirs, files in os.walk(finput):
    for filename in files:
        if filename.lower().endswith(".png"):
            png_files.append(os.path.join(root, filename))

file_count = len(png_files)
text = " Generating Mipmaps for: " + mod_title + " - version: " + version + " "
print("\n" + text.center(len(text) + 20, "-"))
print(" Mipmaps:{0:>2}\n Size:\t detected per image".format(mipmap_levels))
print(" Files:{0:>4}\n".format(file_count))

i = 1
if verbose:
    print("Generating:")
for fp in png_files:
    filename = os.path.basename(fp)
    size_label = file_size(os.path.getsize(fp))
    if verbose:
        print(" {0}: {1} \t {2} {3}".format(i, filename, size_label[0], size_label[1]))
    else:
        print("\rGenerating: [{0}/{1}]".format(i, file_count), end="")

    output_size = create_mipmap(os.path.join(foutput, filename), fp, mipmap_levels)
    if verbose:
        print("    -> {0}x{1}".format(output_size[0], output_size[1]))
    i += 1

success = " All mipmaps generated "
print("\n" + success.center(len(text) + 20, "-") + "\n")
