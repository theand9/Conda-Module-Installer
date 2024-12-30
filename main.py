import sys
import re
import subprocess
import logging
import argparse
from time import sleep
from typing import Optional, Tuple

import requests
from requests import Session, Response
from requests.exceptions import HTTPError, RequestException
from bs4 import BeautifulSoup


BASE_SEARCH_URL = "https://anaconda.org/search?q="
BASE_MODULE_URL = "https://anaconda.org/"
CHANNELS = ["conda-forge", "anaconda", "main", "auto"]
MAX_RETRIES = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def web_request(
    url: str,
    session: Session,
    retries: int = MAX_RETRIES,
) -> Optional[Response]:
    """
    Send a GET request to the specified URL using a requests.Session, with
    an exponential backoff retry mechanism.

    Args:
        url (str): The URL to request.
        session (Session): A requests Session object for reuse.
        retries (int): Number of times to retry before giving up.

    Returns:
        Optional[Response]: A successful Response object, or None if failed.
    """
    for attempt in range(retries):
        try:
            logging.info("Sending GET request to URL: %s", url)
            response = session.get(url, timeout=10)
            response.raise_for_status()
            return response
        except HTTPError as http_err:
            logging.error("HTTP error occurred: %s", http_err)
        except RequestException as req_err:
            logging.error("Request error occurred: %s", req_err)
        except Exception as err:
            logging.error("An unexpected error occurred: %s", err)

        if attempt < retries - 1:
            sleep_time = 2 ** attempt  # exponential backoff
            logging.info("Retrying in %s seconds... (attempt %d/%d)", sleep_time, attempt + 1, retries)
            sleep(sleep_time)
    return None


def search_module(
    module_name: str,
    session: Session
) -> Optional[BeautifulSoup]:
    """
    Searches Anaconda.org for a given module and returns a BeautifulSoup object
    of the search result page if successful.

    Args:
        module_name (str): The name of the module to search for.
        session (Session): A requests Session object for reuse.

    Returns:
        Optional[BeautifulSoup]: Parsed HTML of the search results page, or None if failed.
    """
    search_url = BASE_SEARCH_URL + module_name
    response = web_request(search_url, session)
    if not response:
        logging.error("Failed to retrieve search results for the module '%s'.", module_name)
        return None

    return BeautifulSoup(response.text, "html.parser")


def fetch_module_page(
    module_name: str,
    preferred_channel: Optional[str],
    session: Session
) -> Optional[Tuple[Response, str]]:
    """
    Searches for a module on Anaconda.org and attempts to fetch
    the module page from a valid channel.

    Args:
        module_name (str): The name of the module to install.
        preferred_channel (Optional[str]): User-specified preferred channel.
        session (Session): A requests Session object for reuse.

    Returns:
        Optional[Tuple[Response, str]]: A tuple containing the response and
        the channel name if found, or None if no valid channel is found.
    """
    soup = search_module(module_name, session)
    if not soup:
        return None

    # Identify links and channels from search results
    module_links = soup.select("#search h5 a:nth-child(1)")
    module_channels = soup.select("#search h5 a:nth-child(2) strong")

    if not module_links or not module_channels:
        logging.error("The module '%s' is not available from any valid channel.", module_name)
        return None

    # Build channel priority
    if preferred_channel and preferred_channel in CHANNELS:
        channels = [preferred_channel] + CHANNELS
    else:
        channels = CHANNELS

    # Attempt to fetch each candidate channel's module page
    for channel_tag in module_channels:
        channel = channel_tag.text.strip()
        if channel in channels:
            module_url = f"{BASE_MODULE_URL}{channel}/{module_name}"
            response = web_request(module_url, session)
            if response:
                logging.info("Module '%s' found in channel: %s", module_name, channel)
                return response, channel

    logging.error("The module '%s' is not available from the preferred channels.", module_name)
    return None


def extract_install_command(response: Response) -> Optional[str]:
    """
    Extracts the 'conda install' command from the final webpage.

    Args:
        response (Response): The response object containing the webpage HTML.

    Returns:
        Optional[str]: The installation command, or None if not found.
    """
    soup = BeautifulSoup(response.text, "html.parser")
    # Using a CSS selector with an advanced pseudo-class or built-in check might need a workaround.
    # We'll do a more flexible approach:
    code_elements = soup.find_all("code")
    for elem in code_elements:
        if "conda install" in elem.get_text():
            return elem.get_text().strip()

    logging.error("Failed to retrieve the installation command from the webpage.")
    return None


def validate_install_command(command: str) -> bool:
    """
    Validates the installation command to ensure it is safe and well-formed.

    Args:
        command (str): The command to validate.

    Returns:
        bool: True if valid, False otherwise.
    """
    # Improved validation: check exact structure
    # For example: 'conda install -c conda-forge package_name'
    # We'll do a basic check for now, but you can expand further.
    parts = command.split()
    if len(parts) < 3:
        logging.error("Command structure is too short to be valid.")
        return False
    if parts[0] != "conda" or parts[1] != "install":
        logging.error("Invalid command prefix. Expected 'conda install ...'.")
        return False
    return True


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments for the script.

    Returns:
        argparse.Namespace: Parsed arguments object.
    """
    parser = argparse.ArgumentParser(description="Search and install Python modules from Anaconda.")
    parser.add_argument("module_name", help="The name of the module to search for.")
    parser.add_argument("--channel", help="Preferred channel for installation (e.g., conda-forge).", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Display the installation command without executing it.")
    return parser.parse_args()


def main() -> None:
    """
    Main function that orchestrates searching, retrieving, validating, and installing
    the requested module from Anaconda.
    """
    args = parse_arguments()

    if not args.module_name:
        logging.error("No module name provided. Exiting.")
        sys.exit(1)

    with requests.Session() as session:
        # Retrieve the module page from a valid channel
        result = fetch_module_page(args.module_name, args.channel, session)
        if not result:
            sys.exit(1)

        page_response, channel = result
        install_command = extract_install_command(page_response)
        if not install_command:
            sys.exit(1)

        if not validate_install_command(install_command):
            logging.error("Invalid installation command detected.")
            sys.exit(1)

        logging.info("The module '%s' is available from channel '%s'.", args.module_name, channel)
        if args.dry_run:
            logging.info("Dry run: Installation command is: %s", install_command)
        else:
            logging.info("Executing installation command...")
            try:
                subprocess.run(install_command.split(), check=True)
            except subprocess.CalledProcessError as exc:
                logging.error("Installation command failed: %s", exc)
                sys.exit(1)


if __name__ == "__main__":
    main()
