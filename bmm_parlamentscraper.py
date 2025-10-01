import logging
from typing import Optional
from urllib import response
import requests
import configparser
from jinja2 import Environment, FileSystemLoader, select_autoescape
from bmmbackend import bmmbackend
import sqlite3
import os
import re
import pdfplumber
from difflib import SequenceMatcher
from bmmtools import lemmatize
import xml.etree.ElementTree as ET

conn = sqlite3.connect("checked_items.db")
c = conn.cursor()
c.execute("CREATE TABLE IF NOT EXISTS checked_items (item_id TEXT PRIMARY KEY)")


letter_to_type = {
    "S": "Az Országgyűlés személyi döntését kezdeményező indítvány",
    "Ü": "Az Országgyűlésről szóló törvény 61. § (5) bekezdésében meghatározott kérelem",
    "A": "Azonnali kérdés",
    "B": "Beszámoló",
    "H": "Határozati javaslat",
    "I": "Interpelláció",
    "K": "Kérdés",
    "C": "Népszavazási kezdeményezés",
    "P": "Politikai nyilatkozatra vonatkozó javaslat",
    "V": "Politikai vita kezdeményezése",
    "Y": "Tájékoztató",
    "T": "Törvényjavaslat",
}


def is_checked(item_id):
    c.execute("SELECT 1 FROM checked_items WHERE item_id = ?", (item_id,))
    return c.fetchone() is not None


def mark_checked(item_id):
    c.execute("INSERT OR IGNORE INTO checked_items (item_id) VALUES (?)", (item_id,))
    conn.commit()


config = configparser.ConfigParser()
config.read_file(open("config.ini"))
logging.basicConfig(
    filename=config["DEFAULT"]["logfile_name"],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s | %(module)s.%(funcName)s line %(lineno)d: %(message)s",
)
eventgenerator_api_key = config["DEFAULT"].get("eventgenerator_api_key", None)

session = requests.Session()
if 'proxy_host' in config["Download"] and config["Download"]["proxy_host"] != "":
    proxy_host = config["Download"]["proxy_host"]
    request_proxies: dict[str, str] = {
        "http": "socks5h://" + proxy_host + ":1080",
        "https": "socks5h://" + proxy_host + ":1080",
    }
    session.proxies = request_proxies


if config["DEFAULT"]["donotlemmatize"] == "0":
    import huspacy

    nlp = huspacy.load()
else:
    nlp = None

logging.info("Parlamentscraper started")

backend = bmmbackend(config["DEFAULT"]["monitor_url"], config["DEFAULT"]["uuid"])
env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())
contenttpl = env.get_template("content.html")
contenttpl_keyword = env.get_template("content_keyword.html")

url = config["Download"]["url"]
access_token = config["Download"]["access_token"]
irom_url = config["Download"]["irom_url"]

response = session.get(f"{url}?access_token={access_token}&p_all=F")
logging.info(response.url)

if response.status_code == 200:
    try:
        text = response.content.decode("utf-8")
        iromanyok = ET.fromstring(text)
    except ET.ParseError as e:
        logging.error(f"Failed to parse XML response: {str(e)}")
        exit(1)
    except UnicodeDecodeError as e:
        logging.error(f"Failed to decode response content: {str(e)}")
        exit(1)
else:
    logging.error(
        f"Failed to fetch data from {url}. Status code: {response.status_code}"
    )
    exit(1)

events = backend.getEvents(eventgenerator_api_key)

new_items = []
for iromany in iromanyok:
    izon = iromany.attrib.get("izon", "N/A")
    link = iromany.attrib.get("href", "N/A")
    szam = iromany.attrib.get("szam", " ")
    title = (
        [elem.text for elem in iromany if elem.tag == "cim"][0]
        if any(elem.tag == "cim" for elem in iromany)
        else "N/A"
    )
    irom_path = f"{izon:05}/{izon:05}.pdf"
    pdf_url = f"{irom_url}{irom_path}"
    item = {
        "izon": izon,
        "type": letter_to_type.get(szam[0], None),
        "link": link,
        "title": title,
        "pdf_url": pdf_url,
    }

    key = izon
    if not is_checked(key):
        new_items.append(item)

new_items = new_items[:30]

for item in new_items:
    mark_checked(item["izon"])

doctext_by_uuid = {}
for item in new_items:
    logging.info(f"New item: {item['title']}")
    pdf_url = item["pdf_url"]
    try:
        response = session.get(pdf_url)
        if response.status_code == 200:
            # save pdf
            with open(f"downloads/{item['izon']}.pdf", "wb") as f:
                f.write(response.content)
            doctexts = {}
            pdf_path = os.path.join("downloads", f"{item['izon']}.pdf")
            with pdfplumber.open(pdf_path) as pdf:
                texts = ""
                for page in pdf.pages:
                    texts += page.extract_text() + "\n"
                texts = texts.replace("\n", " ").replace("  ", " ").strip()
                doctexts[pdf_path] = texts
            os.remove(os.path.join("downloads", f"{item['izon']}.pdf"))
            doctext_by_uuid[item["izon"]] = doctexts
        else:
            logging.error(f"Failed to download PDF for item {item['izon']}: HTTP {response.status_code}")
    except Exception as e:
        logging.error(f"Error processing PDF for item {item['izon']}: {str(e)}")
        continue

doctext_by_uuid_lemma = {}
if nlp:
    for izon in doctext_by_uuid:
        doctext_by_uuid_lemma[izon] = {}
        for file in doctext_by_uuid[izon]:
            try:
                doctext_by_uuid_lemma[izon][file] = lemmatize(
                    nlp, doctext_by_uuid[izon][file]
                )
            except Exception as e:
                logging.error(f"Lemmatization failed for {file} in item {izon}: {str(e)}")
                doctext_by_uuid_lemma[izon][file] = []


def serch_multiple(text, keywords, nlp_warn=False):
    results = []
    for keyword in keywords.split(','):
        results += search(text, keyword, nlp_warn)
    return results


def search(text, keyword, nlp_warn=False):
    keyword = keyword.replace('*', '').replace('"', '')
    results = []
    matches = [m.start() for m in re.finditer(re.escape(keyword), text, re.IGNORECASE)]

    words = text.split()

    for match_index in matches:
        # Convert character index to word index
        char_count = 0
        word_index = 0

        for word_index, word in enumerate(words):
            char_count += len(word) + 1  # +1 accounts for spaces
            if char_count > match_index:
                break

        # Get surrounding 10 words before and after the match
        before = " ".join(words[max(word_index - 16, 0) : word_index])
        after = " ".join(words[word_index + 1 : word_index + 17])
        found_word = words[word_index]
        match = SequenceMatcher(
            None, found_word, event["parameters"]
        ).find_longest_match()
        match_before = found_word[: match.a]
        if match_before != "":
            before = before + " " + match_before
        else:
            before = before + " "
        match_after = found_word[match.a + match.size :]
        if match_after != "":
            after = match_after + " " + after
        else:
            after = " " + after
        common_part = found_word[match.a : match.a + match.size]

        if nlp_warn:
            before = "szótövezett találat: " + before

        results.append(
            {
                "file": file,
                "before": before,
                "after": after,
                "common": common_part,
            }
        )
    return results


for event in events["data"]:
    selected_options: Optional[dict[list, str]] = event["selected_options"]
    if type(selected_options) is not dict:
        selected_options = None
    items_lengths = 0
    content = ""
    logging.info(
        f"Processing event {event['id']} - Type: {event['type']} - Parameters: {event['parameters']}"
    )

    for item in new_items:
        title = item["title"]
        pageUrl = item["link"]
        if (
            selected_options
            and "1" in selected_options
            and item["type"]
            and item["type"] not in selected_options["1"]
        ):
            continue
        if event["type"] == 1 and event["parameters"]:
            results = []
            for file in doctext_by_uuid.get(item["izon"], {}):
                text = doctext_by_uuid[item["izon"]][file]
                current_results = serch_multiple(text, event["parameters"])
                if not current_results and nlp:
                    logging.debug(
                        f"No direct match found for '{event['parameters']}' in {file}, trying lemmatized search"
                    )
                    current_results = serch_multiple(
                        " ".join(doctext_by_uuid_lemma[item["izon"]][file]),
                        event["parameters"],
                        nlp_warn=True,
                    )
                results.extend(current_results)

            logging.info(
                f"Found {len(results)} matches for keyword '{event['parameters']}' in item {item['izon']} - {title}"
            )

            res = {
                "title": title,
                "pageUrl": pageUrl,
                "results": results[:5],
                "results_count": len(results),
                "keyword": event["parameters"],
            }
            if len(results) > 0:
                content = content + contenttpl_keyword.render(doc=res)
                items_lengths += 1
                logging.debug(
                    f"Added item to notification content: {title} with {len(results)} matches"
                )
        else:
            res = {
                "title": title,
                "pageUrl": pageUrl,
            }
            content = content + contenttpl.render(doc=res)
            items_lengths += 1
            logging.debug(f"Added item to notification content: {title}")

    if items_lengths > 1:
        content = "Találatok száma: " + str(items_lengths) + "<br>" + content
        logging.info(f"Total matches for event {event['id']}: {items_lengths}")
    elif items_lengths == 0:
        logging.info(f"No matches found for event {event['id']}")

    if config["DEFAULT"]["donotnotify"] == "0" and items_lengths > 0:
        try:
            backend.notifyEvent(event["id"], content, eventgenerator_api_key)
            logging.info(
                f"Successfully notified event {event['id']} with {items_lengths} matches"
            )
        except Exception as e:
            logging.error(f"Failed to notify event {event['id']}: {str(e)}")
    elif items_lengths > 0:
        logging.info(
            f"Notification disabled by config. Would have notified {items_lengths} items for event {event['id']}"
        )

try:
    conn.close()
    logging.info("Database connection closed successfully")
except Exception as e:
    logging.error(f"Error closing database connection: {str(e)}")

logging.info("Parlamentscraper completed successfully")

print("Ready. Bye.")
