# Standard library imports
import requests
from bs4 import BeautifulSoup
import os
import sys
import time
import geoip2.database
from pathlib import Path
import re
import glob

# System configuration to enforce UTF-8 encoding for standard output
sys.stdout.reconfigure(encoding='utf-8')

# ========================
# Directory Configuration
# ========================
PROTOCOLS_DIR = os.path.join("Servers", "Protocols")  # Directory for storing protocol-specific server lists
REGIONS_DIR = os.path.join("Servers", "Regions")      # Directory for storing region-specific server lists
REPORTS_DIR = os.path.join("Servers", "Reports")      # Directory for storing reports and logs
MERGED_DIR = os.path.join("Servers", "Merged")        # Directory for storing merged server lists
CHANNELS_DIR = os.path.join("Servers", "Channels")    # Directory for storing channel-specific server lists

CHANNELS_FILE = "files/telegram_sources.txt"                         # File containing the list of Telegram channels to process

# Create directories if they don't exist
for directory in [PROTOCOLS_DIR, REGIONS_DIR, REPORTS_DIR, MERGED_DIR, CHANNELS_DIR]:
    os.makedirs(directory, exist_ok=True)

# ========================
# Operational Parameters
# ========================
SLEEP_TIME = 1                                          # Time to sleep between processing batches of channels
BATCH_SIZE = 10                                         # Number of channels to process before pausing
FETCH_CONFIG_LINKS_TIMEOUT = 10                         # Timeout for fetching configuration links from channels

MAX_CHANNEL_SERVERS = 100                               # Maximum number of servers to store per channel file
MAX_PROTOCOL_SERVERS = 1000                              # Maximum number of servers to store per protocol file
MAX_REGION_SERVERS = 1000                               # Maximum number of servers to store per region file
MAX_MERGED_SERVERS = 1000                               # Maximum number of servers to store in the merged file

# ========================
# Critical File Paths
# ========================
LOG_FILE = os.path.join(REPORTS_DIR, "extraction_report.log")         # Log file for extraction statistics
GEOIP_DATABASE_PATH = Path("files/db/GeoLite2-Country.mmdb")                         # Path to the GeoIP database
MERGED_SERVERS_FILE = os.path.join(MERGED_DIR, "merged_servers.txt")  # Path to the merged servers file

# ========================
# Protocol Detection Patterns
# ========================
PATTERNS = {
    'vmess': r'(?<![a-zA-Z0-9_])vmess://[^\s<>]+',          # Regex pattern for detecting VMess protocol links
    'vless': r'(?<![a-zA-Z0-9_])vless://[^\s<>]+',          # Regex pattern for detecting VLESS protocol links
    'trojan': r'(?<![a-zA-Z0-9_])trojan://[^\s<>]+',        # Regex pattern for detecting Trojan protocol links
    'hysteria': r'(?<![a-zA-Z0-9_])hysteria://[^\s<>]+',    # Regex pattern for detecting Hysteria protocol links
    'hysteria2': r'(?<![a-zA-Z0-9_])hysteria2://[^\s<>]+',  # Regex pattern for detecting Hysteria2 protocol links
    'tuic': r'(?<![a-zA-Z0-9_])tuic://[^\s<>]+',            # Regex pattern for detecting TUIC protocol links
    'ss': r'(?<![a-zA-Z0-9_])ss://[^\s<>]+',                # Regex pattern for detecting Shadowsocks protocol links
    'wireguard': r'(?<![a-zA-Z0-9_])wireguard://[^\s<>]+',  # Regex pattern for detecting WireGuard protocol links
    'warp': r'(?<![a-zA-Z0-9_])warp://[^\s<>]+'             # Regex pattern for detecting WARP protocol links
}

# ========================
# Core Functions
# ========================
def normalize_telegram_url(url):
    """
    Normalize Telegram URLs to ensure they are in the correct format.
    Converts regular Telegram URLs to their 's/' variant for channel access.
    """
    url = url.strip()
    if url.startswith("https://t.me/"):
        parts = url.split('/')
        if len(parts) >= 4 and parts[3] != 's':
            return f"https://t.me/s/{'/'.join(parts[3:])}"
    return url

def extract_channel_name(url):
    """
    Extract the channel name from a Telegram URL.
    """
    return url.split('/')[-1].replace('s/', '')

def rotate_file(base_path, entries, max_lines, file_prefix):
    """
    Rotate files by splitting entries into multiple files based on the maximum number of lines.
    Old files are deleted before creating new ones.
    """
    file_index = 1
    all_entries = entries.copy()
    
    # Delete old files
    pattern = os.path.join(base_path, f"{file_prefix}*.txt")
    for f in glob.glob(pattern):
        os.remove(f)
    
    # Create new files
    while all_entries:
        chunk = all_entries[:max_lines]
        all_entries = all_entries[max_lines:]
        
        file_name = (
            f"{file_prefix}{file_index}.txt" 
            if file_index > 1 
            else f"{file_prefix}.txt"
        )
        target_path = os.path.join(base_path, file_name)
        
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(chunk) + '\n')
        
        file_index += 1

def count_servers_in_file(file_pattern):
    """
    Count the number of servers in files matching the given pattern.
    """
    total = 0
    for file_path in glob.glob(file_pattern):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                total += len([line for line in f if line.strip()])
        except:
            continue
    return total

def get_current_counts():
    """
    Get the current counts of servers by protocol, region, and total.
    """
    counts = {}
    
    # Count servers by protocol
    for proto in PATTERNS:
        proto_pattern = os.path.join(PROTOCOLS_DIR, f"{proto}*.txt")
        counts[proto] = count_servers_in_file(proto_pattern)
    
    # Count merged servers
    merged_pattern = os.path.join(MERGED_DIR, "merged_servers*.txt")
    counts['total'] = count_servers_in_file(merged_pattern)
    
    # Count servers by region
    country_data = {}
    regional_servers = 0
    for region_file in glob.glob(os.path.join(REGIONS_DIR, "*.txt")):
        country = os.path.basename(region_file).split('.')[0]
        count = count_servers_in_file(region_file)
        country_data[country] = count
        regional_servers += count
    
    counts['successful'] = regional_servers
    counts['failed'] = counts['total'] - regional_servers
    
    return counts, country_data

def process_channel(url):
    """
    Process a Telegram channel to extract and update server configurations.
    """
    existing_configs = load_existing_configs()
    channel_name = extract_channel_name(url)
    channel_file = os.path.join(CHANNELS_DIR, f"{channel_name}.txt")
    
    configs = fetch_config_links(url)
    if not configs:
        return 0, 0

    all_channel_configs = set()
    for proto_links in configs.values():
        all_channel_configs.update(proto_links)

    # Update channel file
    existing_channel_configs = set()
    if os.path.exists(channel_file):
        with open(channel_file, 'r', encoding='utf-8') as f:
            existing_channel_configs = set(f.read().splitlines())
    
    new_channel_configs = all_channel_configs - existing_channel_configs
    if new_channel_configs:
        updated_channel = list(new_channel_configs) + list(existing_channel_configs)
        rotate_file(
            base_path=CHANNELS_DIR,
            entries=updated_channel,
            max_lines=MAX_CHANNEL_SERVERS,
            file_prefix=channel_name
        )

    # Update protocol files
    for proto, links in configs.items():
        if proto == "all":
            continue
        
        proto_pattern = os.path.join(PROTOCOLS_DIR, f"{proto}*.txt")
        existing_entries = []
        for proto_file in glob.glob(proto_pattern):
            with open(proto_file, 'r', encoding='utf-8') as f:
                existing_entries.extend(f.read().splitlines())
        
        new_links = [link for link in links if link not in existing_entries]
        
        if new_links:
            rotate_file(
                base_path=PROTOCOLS_DIR,
                entries=new_links + existing_entries,
                max_lines=MAX_PROTOCOL_SERVERS,
                file_prefix=proto
            )

    # Update merged file
    merged_pattern = os.path.join(MERGED_DIR, "merged_servers*.txt")
    existing_merged = []
    for merged_file in glob.glob(merged_pattern):
        with open(merged_file, 'r', encoding='utf-8') as f:
            existing_merged.extend(f.read().splitlines())
    
    new_merged = [link for link in all_channel_configs if link not in existing_merged]
    if new_merged:
        rotate_file(
            base_path=MERGED_DIR,
            entries=new_merged + existing_merged,
            max_lines=MAX_MERGED_SERVERS,
            file_prefix="merged_servers"
        )

    return 1, len(new_channel_configs)

def fetch_config_links(url):
    """
    Fetch configuration links from a Telegram channel URL.
    """
    try:
        response = requests.get(url, timeout=FETCH_CONFIG_LINKS_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        message_tags = soup.find_all(['div', 'span'], class_='tgme_widget_message_text')
        code_blocks = soup.find_all(['code', 'pre'])
        
        configs = {proto: set() for proto in PATTERNS}
        configs["all"] = set()
        
        for code_tag in code_blocks:
            code_text = code_tag.get_text().strip()
            clean_text = re.sub(r'^(`{1,3})|(`{1,3})$', '', code_text, flags=re.MULTILINE)
            
            for proto, pattern in PATTERNS.items():
                matches = re.findall(pattern, clean_text)
                if matches:
                    configs[proto].update(matches)
                    configs["all"].update(matches)
        
        for tag in message_tags:
            general_text = tag.get_text().strip()
            
            for proto, pattern in PATTERNS.items():
                matches = re.findall(pattern, general_text)
                if matches:
                    configs[proto].update(matches)
                    configs["all"].update(matches)
        
        return {k: list(v) for k, v in configs.items()}
    
    except requests.exceptions.RequestException as e:
        print(f"Connection error for {url}: {e}")
        return None

def load_existing_configs():
    """
    Load existing server configurations from protocol and merged files.
    """
    existing = {proto: set() for proto in PATTERNS}
    existing["merged"] = set()
    
    for proto in PATTERNS:
        proto_pattern = os.path.join(PROTOCOLS_DIR, f"{proto}*.txt")
        for proto_file in glob.glob(proto_pattern):
            try:
                with open(proto_file, 'r', encoding='utf-8') as f:
                    existing[proto].update(f.read().splitlines())
            except Exception as e:
                print(f"Error reading {proto} configs: {e}")
    
    merged_pattern = os.path.join(MERGED_DIR, "merged_servers*.txt")
    for merged_file in glob.glob(merged_pattern):
        try:
            with open(merged_file, 'r', encoding='utf-8') as f:
                existing['merged'].update(f.read().splitlines())
        except Exception as e:
            print(f"Error reading merged configs: {e}")
    
    return existing

def download_geoip_database():
    """
    Download the GeoIP database for geographical analysis.
    """
    GEOIP_URL = "https://git.io/GeoLite2-Country.mmdb"
    GEOIP_DIR = Path("files/db")
    
    try:
        GEOIP_DIR.mkdir(parents=True, exist_ok=True)
        
        response = requests.get(GEOIP_URL, timeout=30)
        response.raise_for_status()
        
        with open(GEOIP_DATABASE_PATH, 'wb') as f:
            f.write(response.content)
            
        print("✅ GeoLite2 database downloaded successfully")
        return True
    
    except Exception as e:
        print(f"❌ Failed to download GeoIP database: {e}")
        return False

def process_geo_data():
    """
    Process geographical data using the GeoIP database.
    """
    if not GEOIP_DATABASE_PATH.exists():
        print("⚠️ GeoIP database missing. Attempting download...")
        success = download_geoip_database()
        if not success:
            return {}
    
    try:
        geo_reader = geoip2.database.Reader(str(GEOIP_DATABASE_PATH))
    except Exception as e:
        print(f"GeoIP database error: {e}")
        return {}

    country_counter = {}  
    
    for region_file in Path(REGIONS_DIR).glob("*.txt"):
        region_file.unlink()

    configs = []
    if os.path.exists(MERGED_SERVERS_FILE):
        with open(MERGED_SERVERS_FILE, 'r', encoding='utf-8') as f:
            configs = [line.strip() for line in f if line.strip()]

    for config in configs:
        try:
            ip = config.split('@')[1].split(':')[0]  
            country_response = geo_reader.country(ip)
            country = country_response.country.name or "Unknown"
            
            country_counter[country] = country_counter.get(country, 0) + 1
            
            region_file = os.path.join(REGIONS_DIR, f"{country}.txt")
            existing_region = []
            if os.path.exists(region_file):
                with open(region_file, 'r', encoding='utf-8') as f:
                    existing_region = f.read().splitlines()
            
            updated_region = [config] + existing_region
            with open(region_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(updated_region[:MAX_REGION_SERVERS]) + '\n')
                
        except (IndexError, geoip2.errors.AddressNotFoundError, ValueError):
            pass
        except Exception as e:
            print(f"Geo processing error: {e}")
    
    geo_reader.close()
    return country_counter

def save_extraction_data(channel_stats, country_data):
    """
    Save extraction statistics and country data to the log file.
    """
    current_counts, country_stats = get_current_counts()
    
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as log:
            log.write("=== Country Statistics ===\n")
            log.write(f"Total Servers: {current_counts['total']}\n")
            log.write(f"Successful Geo-IP Resolutions: {current_counts['successful']}\n")
            log.write(f"Failed Geo-IP Resolutions: {current_counts['failed']}\n")
            for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
                log.write(f"{country:<20} : {count}\n")
            
            log.write("\n=== Server Type Summary ===\n")
            sorted_protocols = sorted(PATTERNS.keys(), key=lambda x: current_counts[x], reverse=True)
            for proto in sorted_protocols:
                log.write(f"{proto.upper():<20} : {current_counts[proto]}\n")
            
            log.write("\n=== Channel Statistics ===\n")
            for channel, total in sorted(channel_stats.items(), key=lambda x: x[1], reverse=True):
                log.write(f"{channel:<20}: {total}\n")
                
    except Exception as e:
        print(f"Error writing to log file: {e}")

def get_channel_stats():
    """
    Get statistics for each channel by counting the number of servers in their respective files.
    """
    channel_stats = {}
    for channel_file in Path(CHANNELS_DIR).glob("*.txt"):
        channel_name = channel_file.stem
        count = count_servers_in_file(str(channel_file))
        channel_stats[channel_name] = count
    return channel_stats

if __name__ == "__main__":
    channels_file = CHANNELS_FILE
    
    try:
        with open(channels_file, 'r', encoding='utf-8') as f:
            raw_urls = [line.strip() for line in f if line.strip()]
        
        normalized_urls = list({normalize_telegram_url(url) for url in raw_urls})
        
        normalized_urls.sort()
        with open(channels_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(normalized_urls))
        
        print(f"✅ Found {len(normalized_urls)} unique channels (standardized)")
        
    except Exception as e:
        print(f"❌ Channel list error: {e}")
        sys.exit(1)

    for idx, channel in enumerate(normalized_urls, 1):
        success, _ = process_channel(channel)
        print(f"⌛ Processed {idx}/{len(normalized_urls)} {channel} ")
        if idx % BATCH_SIZE == 0:
            print(f"⏳ Processed {idx}/{len(normalized_urls)} channels, pausing for {SLEEP_TIME} s 🕐")
            time.sleep(SLEEP_TIME)

    print("🌍 Starting geographical analysis...")
    country_data = process_geo_data()
    
    channel_stats = get_channel_stats()
    save_extraction_data(channel_stats, country_data)

    current_counts, _ = get_current_counts()
    print("\n✅ Extraction Complete")
    print(f"📁 Protocols: {PROTOCOLS_DIR}")
    print(f"🗺 Regions: {REGIONS_DIR}")
    print(f"📄 Merged : {MERGED_DIR}")
    print(f"📂 Channels: {CHANNELS_DIR}")
    print(f"\n📊 Final Statistics:")
    print(f"🎉 Total Servers: {current_counts['total']}")
    print(f"✅ Successful Geo-IP Resolutions: {current_counts['successful']}")
    print(f"❌ Failed Geo-IP Resolutions: {current_counts['failed']}")