import requests
from bs4 import BeautifulSoup
import os
import sys
import time
import geoip2.database
from pathlib import Path

# Initial setup
sys.stdout.reconfigure(encoding='utf-8')

ServerByType = "ServerByType"
sort_by_region_folder = "ServerByRegion"  # New folder for region-based files
os.makedirs(ServerByType, exist_ok=True)
os.makedirs(sort_by_region_folder, exist_ok=True)  # Create ServerByRegion folder

# Global counters
total_servers = 0
successful_servers = 0
failed_servers = 0

# Function to extract V2Ray links from a Telegram URL
def get_v2ray_links(url):
    """Extracts V2Ray links from a Telegram URL."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        soup = BeautifulSoup(response.content, 'html.parser')
        all_tags = soup.find_all(['div', 'span', 'code'], class_='tgme_widget_message_text')
        config_types = {"vmess": set(), "vless": set(), "ss": set(), "trojan": set(), "tuic": set()}
        for tag in all_tags:
            text = tag.get_text()
            for key in config_types:
                if text.startswith(f"{key}://"):
                    config_types[key].add(text)
                    break
        return {k: list(v) for k, v in config_types.items()}
    except requests.exceptions.RequestException as e:
        print(f"Request exception for {url}: {e}")
        return None

# Function to load existing servers from files
def load_existing_servers():
    """Loads existing servers from files."""
    existing_servers = {"vmess": set(), "vless": set(), "ss": set(), "trojan": set(), "tuic": set()}
    for key in existing_servers:
        file_path = os.path.join(ServerByType, f"{key}.txt")
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_servers[key] = set(f.read().splitlines())
            except Exception as e:
                print(f"Error reading existing servers from {file_path}: {e}")
                existing_servers[key] = set()  # If file reading fails, just use empty set
    return existing_servers

# Function to count servers in files
def count_servers_in_files():
    """Counts the number of servers in files."""
    server_counts = {"vmess": 0, "vless": 0, "ss": 0, "trojan": 0, "tuic": 0}
    for key in server_counts:
        file_path = os.path.join(ServerByType, f"{key}.txt")
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    server_counts[key] = len(f.readlines())
            except Exception as e:
                print(f"Error counting servers from {file_path}: {e}")
                server_counts[key] = 0
    return server_counts

# Function to read Telegram channels from a file and remove duplicates
def read_telegram_channels(file_path):
    """Reads Telegram channels from a file and removes duplicates."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            channels = [line.strip() for line in f.readlines() if line.strip()]
        unique_channels = list(set(channels))
        unique_channels.sort()
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(unique_channels) + "\n")
        except Exception as e:
            print(f"Error writing to telegram channels file: {e}")
        print(f"✅ Duplicate channels removed. Total unique channels: {len(unique_channels)}")
        return unique_channels
    except FileNotFoundError:
        print(f"❌ File {file_path} not found.")
        return []
    except Exception as e:
        print(f"❌ An error occurred while reading the file: {e}")
        return []

# Function to update channel statistics
def update_channel_stats(channel_url, new_servers_count, successful, failed):
    """Updates the channel statistics."""
    if channel_url in channel_stats:
        channel_stats[channel_url]["total_servers"] += new_servers_count
        channel_stats[channel_url]["count"] += new_servers_count
        channel_stats[channel_url]["successful"] += successful
        channel_stats[channel_url]["failed"] += failed
    else:
        channel_stats[channel_url] = {
            "total_servers": new_servers_count,
            "count": new_servers_count,
            "successful": successful,
            "failed": failed
        }

# Update after each channel extraction
def extract_and_update_channel(url):
    """Extract servers from the Telegram channel and update statistics."""
    successful_new, failed_new, total_new_servers = 0, 0, 0

    configs = get_v2ray_links(url)

    if configs:
        successful_new = 1
        total_new_servers = 0

        for key, values in configs.items():
            # Deduplicate within this channel extraction
            new_servers = set(values) - existing_servers[key]
            if not new_servers:
                continue

            # Check for duplicates again (in case of concurrent runs, etc.)
            filtered_servers = set()
            for server in new_servers:
                if server not in existing_servers[key]:
                    filtered_servers.add(server)

            new_servers_count = len(filtered_servers)
            server_counts_new[key] += new_servers_count
            total_new_servers += new_servers_count
            existing_servers[key].update(filtered_servers)  # Update existing_servers with the new ones

            try:
                with open(os.path.join(ServerByType, f"{key}.txt"), 'a', encoding='utf-8') as f:
                    for server in filtered_servers:
                        f.write(server + '\n')  # Write new servers to the file
            except Exception as e:
                print(f"Error writing to {key}.txt: {e}")

        update_channel_stats(url, total_new_servers, successful_new, failed_new)
        print(f"✅ {url} - New servers: {total_new_servers}")
    else:
        failed_new = 1
        print(f"❌ {url}")

    return successful_new, failed_new, total_new_servers

# Region-based functions
def extract_server_ip(config: str) -> str:
    """Extracts IP from V2Ray link"""
    try:
        parts = config.split('@')
        if len(parts) > 1:
            ip_and_port = parts[1].split(':')
            if len(ip_and_port) > 1:
                return ip_and_port[0]
    except Exception as e:
        print(f"Error extracting IP from config '{config}': {e}")
    return None

def get_country_from_ip(reader, ip_address: str) -> str:
    """Gets country from IP using GeoLite2 database"""
    try:
        response = reader.country(ip_address)
        country_name = response.country.name
        return country_name if country_name else "Unknown"
    except geoip2.errors.AddressNotFoundError:
        print(f"IP address {ip_address} not found in the database.")
        return "Unknown"
    except Exception as e:
        print(f"Error retrieving country for IP {ip_address}: {e}")
        return "Unknown"

def save_configs_by_region(configs: list, reader):
    """Saves configs to region-based files"""
    for config in configs:
        ip = extract_server_ip(config)
        if ip:
            region = get_country_from_ip(reader, ip)
            if region != "Unknown":
                file_path = os.path.join(sort_by_region_folder, f"{region}.txt")  # Save in ServerByRegion folder
                try:
                    with open(file_path, 'a', encoding='utf-8') as f:
                        f.write(config + '\n')
                except Exception as e:
                    print(f"Error writing to region file: {e}")
            else:
                failed_servers += 1
        else:
            failed_servers += 1

# Main program
if __name__ == "__main__":
    telegram_channels_file = "telegram_channels.txt"
    telegram_urls = read_telegram_channels(telegram_channels_file)
    if not telegram_urls:
        print("❌ The list of channels is empty. The program has stopped.")
        sys.exit(1)

    existing_server_counts = count_servers_in_files()

    server_counts_new = {"vmess": 0, "vless": 0, "ss": 0, "trojan": 0, "tuic": 0}
    total_configs_new, successful_new, failed_new = 0, 0, 0
    channel_stats = {}

    batch_size = 10
    batch_count = 0

    # Extract from Telegram
    for url in telegram_urls:
        successful, failed, total_new = extract_and_update_channel(url)
        total_configs_new += total_new
        successful_new += successful
        failed_new += failed

        batch_count += 1
        if batch_count >= batch_size:
            print("⏳ Waiting for 10 seconds before continuing...")
            time.sleep(10)
            batch_count = 0

    print(f"🎉 Extraction completed. All files have been saved in the {ServerByType} folder.")

    # Region-based processing
    try:
        reader = geoip2.database.Reader("GeoLite2-Country.mmdb")
    except FileNotFoundError:
        print("❌ GeoLite2 database file not found. Please download and place it in the specified path.")
        sys.exit(1)
    except Exception as e:
        print(f"Error opening GeoLite2 database: {e}")
        sys.exit(1)

    v2ray_configs = get_v2ray_links_from_folder(Path(ServerByType))
    total_servers = len(v2ray_configs)

    if v2ray_configs:
        save_configs_by_region(v2ray_configs, reader)
        print("✅ Configs saved successfully based on region.")
    else:
        print("❌ No V2Ray configs found.")

    reader.close()
