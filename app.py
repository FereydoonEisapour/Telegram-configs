import requests
from bs4 import BeautifulSoup
import os
import sys
import time
import geoip2.database
from pathlib import Path
import shutil

# Initial setup
sys.stdout.reconfigure(encoding='utf-8')

ServerByType = "ServerByType"
sort_by_region_folder = "ServerByRegion"  # New folder for region-based files
log_folder = "Log"  # New folder for log files
os.makedirs(ServerByType, exist_ok=True)
os.makedirs(sort_by_region_folder, exist_ok=True)  # Create ServerByRegion folder
os.makedirs(log_folder, exist_ok=True)  # Create Log folder
log_file = os.path.join(log_folder, "ExtractionReport.log")  # Log file in Log folder
GEOIP_DATABASE_PATH = Path("database_path/GeoLite2-Country.mmdb")

# Global counters
total_servers = 0
successful_servers = 0
failed_servers = 0

# Function to read previous data from the log file
def read_previous_data():
    """Reads previous data from the log file."""
    prev_server_counts_new = {"vmess": 0, "vless": 0, "ss": 0, "trojan": 0, "tuic": 0}
    prev_total_configs_new, prev_successful_new, prev_failed_new = 0, 0, 0
    prev_channel_stats = {}
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Error reading log file: {e}")
            return prev_server_counts_new, prev_total_configs_new, prev_successful_new, prev_failed_new, prev_channel_stats

        section = None
        for line in lines:
            line = line.strip()
            if line.startswith("==="):
                section = line
            elif section == "=== Server Type Summary New ===":
                parts = line.split(" Servers: ")
                if len(parts) == 2 and parts[0].lower() in prev_server_counts_new:
                    try:
                        prev_server_counts_new[parts[0].lower()] = int(parts[1])
                    except ValueError:
                        print(f"Invalid value for server count: {parts[1]}")
                        pass  # Keep the default value of 0
            elif section == "=== Extraction Summary New ===":
                if line.startswith("Total Extracted Servers:"):
                    try:
                        prev_total_configs_new = int(line.split(": ")[1])
                    except ValueError:
                        print(f"Invalid value for total configs: {line.split(': ')[1]}")
                        pass
                elif line.startswith("Successful Channels:"):
                    try:
                        prev_successful_new = int(line.split(": ")[1])
                    except ValueError:
                        print(f"Invalid value for successful channels: {line.split(': ')[1]}")
                        pass
                elif line.startswith("Failed Channels:"):
                    try:
                        prev_failed_new = int(line.split(": ")[1])
                    except ValueError:
                        print(f"Invalid value for failed channels: {line.split(': ')[1]}")
                        pass

            elif section == "=== Channel Statistics ===":
                parts = line.split(": ")
                if len(parts) == 5:
                    channel_name = parts[0].strip()
                    try:
                        prev_channel_stats[channel_name] = {
                            "total_servers": int(parts[1].split()[0]),
                            "count": int(parts[2].split()[0]),
                            "successful": int(parts[3].split()[0]),
                            "failed": int(parts[4].split()[0])
                        }
                    except ValueError as e:
                        print(f"Error parsing channel stats for {channel_name}: {e}")
                        pass  # Ignore and continue

    return prev_server_counts_new, prev_total_configs_new, prev_successful_new, prev_failed_new, prev_channel_stats

# Function to save updated data to the log file
def save_updated_data(server_counts_new, server_counts_all, total_configs_new, total_configs_all,
                      successful_new, successful_all, failed_new, failed_all, channel_stats):
    """Saves updated data to the log file."""
    try:
        with open(log_file, 'w', encoding='utf-8') as lf:
            lf.write("=== Server Type Summary New ===\n")
            for key, count in server_counts_new.items():
                lf.write(f"{key.upper():<12} Servers: {count}\n")
            lf.write("\n")

            lf.write("=== Server Type Summary All ===\n")
            for key, count in server_counts_all.items():
                lf.write(f"{key.upper():<12} Servers: {count}\n")
            lf.write("\n")

            lf.write("=== Extraction Summary New ===\n")
            lf.write(f"Total Extracted Servers: {total_configs_new}\n")
            lf.write(f"Successful Channels:     {successful_new}\n")
            lf.write(f"Failed Channels:         {failed_new}\n")
            lf.write("\n")

            lf.write("=== Extraction Summary All ===\n")
            lf.write(f"Total Extracted Servers: {total_configs_all}\n")
            lf.write(f"Successful Channels:     {successful_all}\n")
            lf.write(f"Failed Channels:         {failed_all}\n")
            lf.write("\n")

            lf.write("=== Channel Statistics ================== \n")
            lf.write("Telegram Channels Names============Total Servers === New Servers === Successful === Failed \n")
            sorted_stats = sorted(channel_stats.items(), key=lambda x: x[1]["total_servers"], reverse=True)
            for channel, data in sorted_stats:
                lf.write(f"{channel:<35}:      {data['total_servers']:<6} ===      {data['count']:<6} ===     {data['successful']:<4} ===   {data['failed']:<4}\n")
    except Exception as e:
        print(f"Error writing to log file: {e}")

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

def update_country_count(country: str):
    """Updates country statistics"""
    global successful_servers, failed_servers
    country_count_file = os.path.join(log_folder, "country_count.log")  # Country count file in Log folder
    country_count = {}

    if os.path.exists(country_count_file):
        try:
            with open(country_count_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines[2:]:
                    parts = line.strip().split(': ')  # Split by ': ' instead of ','
                    if len(parts) == 2:
                        country_name = parts[0]
                        try:
                            count = int(parts[1])
                        except ValueError:
                            print(f"Invalid count value in country count file: {parts[1]}")
                            count = 0
                        country_count[country_name] = count
        except Exception as e:
            print(f"Error loading country count file: {e}")

    country_count[country] = country_count.get(country, 0) + 1
    sorted_country_count = sorted(country_count.items(), key=lambda x: x[1], reverse=True)
    successful_servers = sum(count for _, count in sorted_country_count)

    try:
        with open(country_count_file, 'w', encoding='utf-8') as f:
            f.write(f"All servers: {total_servers}\n")
            f.write(f"Successful: {successful_servers}\n")
            f.write(f"Failed: {failed_servers}\n")
            f.write("===== Countries =====\n")
            for country_name, count in sorted_country_count:
                f.write(f"{country_name}: {count}\n")  # Write in the format "Country: Count"
    except Exception as e:
        print(f"Error writing to country count file: {e}")

def get_v2ray_links_from_folder(folder_path: Path) -> list:
    """Reads V2Ray links from text files in folder"""
    v2ray_links = []
    for file in folder_path.glob("*.txt"):
        if file.name in ["ExtractionReport.log", "country_count.log", "invalid_links.txt"]:
            continue
        try:
            with file.open('r', encoding='utf-8') as f:
                links = [line.strip() for line in f if line.strip()]
                v2ray_links.extend(links)
        except Exception as e:
            print(f"Error reading from file {file.name}: {e}")
    return v2ray_links

def save_configs_by_region(configs: list, reader):
    """Saves configs to region-based files"""
    global failed_servers
    invalid_links_file = os.path.join(ServerByType, "invalid_links.txt")

    for config in configs:
        ip = extract_server_ip(config)
        if ip:
            region = get_country_from_ip(reader, ip)
            if region != "Unknown":
                file_path = os.path.join(sort_by_region_folder, f"{region}.txt")  # Save in ServerByRegion folder
                try:
                    with open(file_path, 'a', encoding='utf-8') as f:
                        f.write(config + '\n')
                    update_country_count(region)
                except Exception as e:
                    print(f"Error writing to region file: {e}")
            else:
                failed_servers += 1
        else:
            failed_servers += 1
            try:
                with open(invalid_links_file, 'a', encoding='utf-8') as f:
                    f.write(config + '\n')
            except Exception as e:
                print(f"Error writing to invalid links file: {e}")

# Main program
if __name__ == "__main__":
    telegram_channels_file = "telegram_channels.txt"
    telegram_urls = read_telegram_channels(telegram_channels_file)
    if not telegram_urls:
        print("❌ The list of channels is empty. The program has stopped.")
        sys.exit(1)

    prev_server_counts_new, prev_total_configs_new, prev_successful_new, prev_failed_new, prev_channel_stats = read_previous_data()
    existing_server_counts = count_servers_in_files()

    server_counts_new = {"vmess": 0, "vless": 0, "ss": 0, "trojan": 0, "tuic": 0}
    total_configs_new, successful_new, failed_new = 0, 0, 0
    channel_stats = prev_channel_stats.copy()
    existing_servers = load_existing_servers()

    batch_size = 5
    batch_count = 0

    # Extract from Telegram
    for url in telegram_urls:
        successful, failed, total_new = extract_and_update_channel(url)
        total_configs_new += total_new
        successful_new += successful
        failed_new += failed

        server_counts_all = {key: existing_server_counts[key] + server_counts_new[key] for key in server_counts_new}
        total_configs_all = prev_total_configs_new + total_configs_new
        successful_all = prev_successful_new + successful_new
        failed_all = prev_failed_new + failed_new

        save_updated_data(server_counts_new, server_counts_all, total_configs_new, total_configs_all,
                          successful_new, successful_all, failed_new, failed_all, channel_stats)

        batch_count += 1
        if batch_count >= batch_size:
            print("⏳ Waiting for 20 seconds before continuing...")
            time.sleep(20)
            batch_count = 0

    print(f"🎉 Extraction completed. All files have been saved in the {ServerByType} folder.")

    # Region-based processing
    try:
        reader = geoip2.database.Reader(GEOIP_DATABASE_PATH)
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
