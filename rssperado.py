import io
import os
import gc
import sys
import json
import urllib
import hashlib
import argparse
import timeago
import feedparser
import requests
import concurrent.futures
import warnings

import numpy as np
import argostranslate.package
import argostranslate.translate

from urllib.parse import urlparse
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dateutil.parser import parse as dateutil_parse
from PIL import Image, ImageOps
from langdetect import detect as langdetect_detect
from transformers import pipeline

# Global variables.
ARGS = None
ner_classifier = None

# Disable unnecessary warnings.
warnings.simplefilter("ignore", category=UserWarning, append=True)


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.int_, np.intc, np.intp, np.int8, np.int16, np.int32, np.int64, np.uint8, np.uint16, np.uint32, np.uint64)):
            return int(obj)
        elif isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)


def read_urls(input_file: str) -> list:
    """Read a list of RSS feed URLs from a file."""
    urls = []
    with open(input_file, "r") as f:
        for line in f:
            urls.append(line.rstrip())
    return urls


def process_feed(feed_url: str) -> list:
    """ Process RSS feed and return list of stories. """
    feed_entries = []

    try:
        if ARGS.verbose:
            print("> processing > {} ".format(feed_url))

        feed_data = feedparser.parse(feed_url)
        for feed_entry in feed_data.entries[:ARGS.max_entries]:
            feed_entries.append(feed_entry)
    except Exception as e:
        if ARGS.verbose:
            print(e)

    return feed_entries


def extract_podcast_url_from_unprocessed_feed_entry(entry: object) -> str:
    """ Extracts podcast url from the unprocessed feed entry. """

    if "links" in entry:
        for link in entry["links"]:
            if "rel" in link and "type" in link and "href" in link:
                if link["rel"] == "enclosure" and link["type"] == "audio/mpeg":
                    return link["href"]

    return None


def extract_image_url_from_unprocessed_feed_entry(entry) -> str:
    """ Extracts image url from the unprocessed feed entry. """

    # Try getting image from first element in `media_thumbnail`.
    if "media_thumbnail" in entry:
        image_url = entry["media_thumbnail"]
        if len(image_url) > 0 and "url" in image_url[0]:
            return image_url[0]["url"]

    # Try getting image from first element in `media_content`.
    if "media_content" in entry:
        image_url = entry["media_content"]
        if len(image_url) > 0 and "url" in image_url[0]:
            return image_url[0]["url"]

    # Try getting image from enclosure element.
    if "links" in entry:
        for link in entry["links"]:
            if "rel" in link and "type" in link and "href" in link:
                if link["rel"] == "enclosure" and link["type"] == "image/jpeg":
                    return link["href"]

    # Try getting meta from OpenGraph as a fallback option.
    if ARGS.og_images:
        try:
            response = requests.get(entry["link"], timeout=1)
            soup = BeautifulSoup(response.text, "html.parser")

            meta_items = soup.select("meta[property]")
            for meta_item in meta_items:
                if meta_item["property"] == "og:image" and meta_item["content"]:
                    # Clear from memory.
                    del (response)
                    del (soup)
                    gc.collect()

                    return meta_item["content"]
        except Exception as e:
            if ARGS.verbose:
                print("ERROR: {}".format(e))

    return None


def fetch_and_resize_image(image_url, desired_image_filename) -> bool:
    """ Fetches image from remote URL and resizes and then saves locally. """
    if not os.path.exists("{}/images/{}".format(ARGS.output_dir, desired_image_filename)):
        try:
            req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
            path = io.BytesIO(urllib.request.urlopen(req, timeout=5).read())

            with Image.open(path) as im:
                resized = ImageOps.contain(im, (ARGS.image_width, ARGS.image_height))
                resized.save("{}/images/{}".format(
                    ARGS.output_dir,
                    desired_image_filename),
                    quality=ARGS.image_quality)

            # Clear from memory.
            del (im)
            del (req)
            del (path)
            gc.collect()

            return True
        except Exception as e:
            if ARGS.verbose:
                print("  > resize failed for > {} > {}".format(image_url, e))
            return False
    return True


def process_feed_entry(entry: object, idx: int, num_of_unprocessed_feed_entries: int) -> object:
    """ Process RSS feed entry and return processed story. """
    story = {
        "guid": None,
        "link": None,
        "title": {
            "origin": None,
            "en": None,
        },
        "summary": {
            "origin": None,
            "en": None,
        },
        "published": {
            "ago": None,
            "dt": None,
        },
        "ner": None,
        "image_filename": None,
        "podcast_url": None,
        "source": None,
        "type": "story",
        "origin_language": None,
    }

    # If there is no link present they just exit!
    if "link" not in entry:
        return False

    # Generating UID from link.
    story["guid"] = hashlib.md5(entry["link"].encode()).hexdigest()

    # Extracting link.
    story["link"] = entry["link"]

    # Extracting and cleaning up source.
    story["source"] = urlparse(entry["link"]).netloc

    # Extracting and cleaning up title.
    story["title"]["origin"] = entry["title"] if "title" in entry else None
    if story["title"]["origin"]:
        try:
            story["title"]["origin"] = BeautifulSoup(story["title"]["origin"], features="html.parser").get_text()
        except Exception as e:
            story["title"]["origin"] = None
            if ARGS.verbose:
                print("Error parsing published date: {}".format(e))

    # Extracting and cleaning up summary.
    story["summary"]["origin"] = entry["summary"] if "summary" in entry else None
    if story["summary"]["origin"]:
        try:
            story["summary"]["origin"] = BeautifulSoup(story["summary"]["origin"], features="html.parser").get_text()
        except Exception as e:
            story["summary"]["origin"] = None
            if ARGS.verbose:
                print("Error getting summary: {}".format(e))

    # Extracting and formatting published date.
    entry_published = entry["published"] if "published" in entry else None
    if entry_published:
        try:
            story["published"]["dt"] = str(entry_published)
            story["published"]["ago"] = str(timeago.format(
                dateutil_parse(entry_published).replace(tzinfo=None),
                datetime.now().replace(tzinfo=None)))
        except Exception as e:
            story["published"] = None
            if ARGS.verbose:
                print("Error parsing published date: {}".format(e))

    # Detect if story is a podcast by searching for `itunes` word in elements.
    for entry_key in entry.keys():
        if entry_key.find("itunes") == 0:
            story["type"] = "podcast"

    try:
        print("[{}/{}] {} :: {} > {}".format(
            idx+1,
            num_of_unprocessed_feed_entries,
            story["type"],
            story["source"],
            entry["title"][:50],
        ))
    except Exception as e:
        pass

    # If the story is a podcast try to extract podcast url.
    if story["type"] == "podcast":
        podcast_url = extract_podcast_url_from_unprocessed_feed_entry(entry)
        if podcast_url:
            story["podcast_url"] = podcast_url

    # If the story is not podcast try to find image associated with this story.
    if story["type"] == "story":
        image_url = extract_image_url_from_unprocessed_feed_entry(entry)

        # Fetch and resize the image and store them locally.
        if image_url and ARGS.fetch_images:
            desired_image_filename = "{}.jpg".format(story["guid"])

            success = fetch_and_resize_image(image_url, desired_image_filename)
            if success:
                story["image_filename"] = desired_image_filename
            else:
                story["image_filename"] = None

    # Detects the language of the story.
    if story["title"]["origin"] and story["summary"]["origin"]:
        try:
            story["origin_language"] = langdetect_detect("{} {}".format(
                story["title"]["origin"],
                story["summary"]["origin"],
            ))
        except Exception as e:
            story["origin_language"] = None
            if ARGS.verbose:
                print("Error detecting language: {}".format(e))

    # If language is already English, skip translation.
    if story["origin_language"] == "en":
        story["title"]["en"] = story["title"]["origin"]
        story["summary"]["en"] = story["summary"]["origin"]

    # If language is not English, translate it.
    if story["origin_language"] != "en" and ARGS.translate:
        try:
            story["title"]["en"] = argostranslate.translate.translate(story["title"]["origin"], story["origin_language"], "en")
            story["summary"]["en"] = argostranslate.translate.translate(story["summary"]["origin"], story["origin_language"], "en")
        except Exception as e:
            story["title"]["en"] = None
            story["summary"]["en"] = None
            if ARGS.verbose:
                print("Error translating: {}".format(e))

    # Does the NER extraction.
    if story["title"]["en"] and story["summary"]["en"] and ARGS.ner:
        try:
            story["ner"] = ner_classifier("{} {}".format(
                story["title"]["en"],
                story["summary"]["en"],
            ))
        except Exception as e:
            story["ner"] = None
            if ARGS.verbose:
                print("Error extracting NER: {}".format(e))

    return story


def print_divider(char: str = "+"):
    """ Prints a divider. """
    print(char*80)


if __name__ == "__main__":
    program_description = [
        "The RSS parser is a command-line utility that simplifies the process of parsing RSS feeds,",
        "enriching the extracted data, exporting it to JSON files, enabling content classification,",
        "and providing English translation."
    ]
    # Parse command line arguments.
    parser = argparse.ArgumentParser(description=" ".join(program_description))
    parser.add_argument("--input-feeds", help="specify input file with the list of RSS feeds", required=True)
    parser.add_argument("--output-dir", help="specify output directory (default: ./output)", default="./output")
    parser.add_argument("--max-entries", help="specify max feed entries to parse (default: 50)", type=int, default=50)

    parser.add_argument("--fetch-images", help="fetches images from feed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--og-images", help="fetched images from OG meta tags as fallback", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resize", help="resizes images to specific dimensions", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--image-width", help="specify resized image width (default: 800)", type=int, default=800)
    parser.add_argument("--image-height", help="specify resized image height (default: 600)", type=int, default=600)
    parser.add_argument("--image-quality", help="specify resized image quality (default: 90)", type=int, default=90)

    parser.add_argument("--translate", help="translate each story to English", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--ner", help="enables NER classification", action=argparse.BooleanOptionalAction, default=False)

    parser.add_argument("--verbose", help="make the operation more talkative", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument('--version', action='version', version='%(prog)s 1.0')
    args = parser.parse_args()
    ARGS = args

    # Check if input file exists.
    if not os.path.exists(args.input_feeds):
        print("ERROR: Input file {} does not exist.".format(args.input_feeds))
        sys.exit(1)

    # Create output directory and image directory if it doesn't exist.
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir, exist_ok=True)
        os.makedirs("{}/{}".format(args.output_dir, "images"), exist_ok=True)

    # Print all the arguments with space between them.
    print_divider()
    print("Arguments:")
    for arg in vars(args):
        print("  - {:<15}: {}".format(arg, getattr(args, arg)))
    print_divider()

    # Checks if translate is enabled.
    if ARGS.ner and not ARGS.translate:
        print("ERROR: NER classification requires translation (--translate).")
        sys.exit(1)

    # Enables NER classification.
    if ARGS.ner:
        ner_classifier = pipeline("ner", grouped_entities=True, model="dbmdz/bert-large-cased-finetuned-conll03-english")

    # Enables translation and downloads the models.
    if ARGS.translate:
        argostranslate.package.update_package_index()
        available_packages = argostranslate.package.get_available_packages()
        language_list = ["ar", "az", "ca", "zh", "cs", "da", "nl", "eo", "fi", "fr", "de", "el", "he", "hi", "hu", "id", "ga", "it", "ja", "ko", "fa", "pl", "pt", "ru", "sk", "es", "sv", "th", "tr"]
        print("Updating translation models:")
        for idx, from_code in enumerate(language_list):
            to_code = "en"
            package_to_install = next(
                filter(
                    lambda x: x.from_code == from_code and x.to_code == to_code, available_packages
                )
            )
            print("  - [{}/{}] {}".format(idx+1, len(language_list), package_to_install))
            argostranslate.package.install_from_path(package_to_install.download())
        print_divider()

    # Read the list of RSS feeds.
    urls = read_urls(args.input_feeds)

    # Print all the URLs.
    print("Feeds:")
    for url in urls:
        print("  - {}".format(url))
    print_divider()

    # Process each feed.
    for url in urls:
        processed_feed_entries = []
        unprocessed_feed_entries = process_feed(url)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []

            # Spawn a thread per RSS feed.
            num_of_unprocessed_feed_entries = len(unprocessed_feed_entries)
            for idx, entry in enumerate(unprocessed_feed_entries):
                futures.append(executor.submit(process_feed_entry, entry, idx, num_of_unprocessed_feed_entries))

            # Hydrate all the feed data from spawned futures.
            for future in concurrent.futures.as_completed(futures):
                process_feed_entry_results = future.result()
                if process_feed_entry_results:
                    processed_feed_entries.append(process_feed_entry_results)

        # Save raw processed data to json file.
        url_hash = hashlib.md5(url.encode()).hexdigest()
        with open("{}/{}.json".format(args.output_dir, url_hash), "w", encoding="utf-8") as fp:
            json.dump(processed_feed_entries, fp, default=str)

    # Clear from memory.
    gc.collect()
