#!/usr/bin/env python3
"""
LinkPulse CLI - YouTube and Blog Post Affiliate Link Checker
"""

import argparse
import asyncio
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

try:
    from googleapiclient.discovery import build
    YOUTUBE_API_AVAILABLE = True
except ImportError:
    YOUTUBE_API_AVAILABLE = False


class Config:
    """Configuration handler for LinkPulse"""
    
    def __init__(self, config_path: str = 'config.yaml'):
        self.config_path = config_path
        self.data = self._load_config()
    
    def _load_config(self) -> dict:
        """Load and validate configuration from YAML file"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            # Validate required sections
            if 'sources' not in config:
                raise ValueError("Config must contain 'sources' section")
            
            # Set defaults
            settings = config.get('settings', {})
            config['settings'] = {
                'concurrent_requests': settings.get('concurrent_requests', 3),
                'request_timeout': settings.get('request_timeout', 30),
                'retry_attempts': settings.get('retry_attempts', 3),
                'delay_between_requests': settings.get('delay_between_requests', 1.5),
                'youtube_api_key': settings.get('youtube_api_key'),
                'max_videos_per_channel': settings.get('max_videos_per_channel', 50),
                'max_posts_per_domain': settings.get('max_posts_per_domain', 100),
                'crawl_depth': settings.get('crawl_depth', 2),
                'days_back': settings.get('days_back', 180),
            }
            
            return config
            
        except FileNotFoundError:
            print(f"❌ Error: Configuration file '{self.config_path}' not found")
            print("\nCreate a config.yaml file with your YouTube videos and blog posts.")
            print("See README.md for example configuration.")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"❌ Error: Invalid YAML in '{self.config_path}': {e}")
            sys.exit(1)
        except ValueError as e:
            print(f"❌ Error: {e}")
            sys.exit(1)


class ChannelScraper:
    """YouTube channel scraping functionality"""
    
    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose
        self.session = requests.Session()
        
        # Initialize YouTube API if available
        self.youtube_service = None
        if (YOUTUBE_API_AVAILABLE and 
            self.config.data['settings'].get('youtube_api_key')):
            try:
                self.youtube_service = build(
                    'youtube', 'v3',
                    developerKey=self.config.data['settings']['youtube_api_key']
                )
            except Exception as e:
                if self.verbose:
                    print(f"⚠️  YouTube API initialization failed: {e}")
    
    def extract_channel_id(self, channel_url: str) -> Optional[str]:
        """Extract channel ID from various YouTube channel URL formats"""
        patterns = [
            r'youtube\.com/channel/([a-zA-Z0-9_-]+)',
            r'youtube\.com/c/([a-zA-Z0-9_-]+)',  
            r'youtube\.com/user/([a-zA-Z0-9_-]+)',
            r'youtube\.com/@([a-zA-Z0-9_.-]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, channel_url)
            if match:
                return match.group(1)
        return None
    
    def get_channel_videos_api(self, channel_url: str) -> List[Dict]:
        """Get channel videos using YouTube API"""
        if not self.youtube_service:
            return []
        
        channel_id = self.extract_channel_id(channel_url)
        if not channel_id:
            return []
        
        videos = []
        max_videos = self.config.data['settings']['max_videos_per_channel']
        
        try:
            # First get channel info to handle different URL formats
            if '/user/' in channel_url or '/c/' in channel_url or '/@' in channel_url:
                # Convert username/custom URL to channel ID
                if '/user/' in channel_url:
                    response = self.youtube_service.channels().list(
                        part='id',
                        forUsername=channel_id
                    ).execute()
                else:
                    # For custom URLs, search by channel name
                    response = self.youtube_service.search().list(
                        part='snippet',
                        q=channel_id,
                        type='channel',
                        maxResults=1
                    ).execute()
                    
                if response['items']:
                    channel_id = response['items'][0]['id']['channelId'] if 'id' in response['items'][0] else response['items'][0]['id']
            
            # Get channel's uploads playlist
            channel_response = self.youtube_service.channels().list(
                part='contentDetails',
                id=channel_id
            ).execute()
            
            if not channel_response['items']:
                return []
            
            uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
            
            # Get videos from uploads playlist
            next_page_token = None
            while len(videos) < max_videos:
                playlist_response = self.youtube_service.playlistItems().list(
                    part='snippet',
                    playlistId=uploads_playlist_id,
                    maxResults=min(50, max_videos - len(videos)),
                    pageToken=next_page_token
                ).execute()
                
                for item in playlist_response['items']:
                    video_id = item['snippet']['resourceId']['videoId']
                    video_title = item['snippet']['title']
                    video_url = f"https://youtube.com/watch?v={video_id}"
                    
                    videos.append({
                        'url': video_url,
                        'title': video_title,
                        'published': item['snippet']['publishedAt']
                    })
                
                next_page_token = playlist_response.get('nextPageToken')
                if not next_page_token:
                    break
                    
        except Exception as e:
            if self.verbose:
                print(f"⚠️  YouTube API channel scraping failed: {e}")
        
        return videos
    
    def get_channel_videos_scraping(self, channel_url: str) -> List[Dict]:
        """Get channel videos using web scraping as fallback"""
        videos = []
        max_videos = self.config.data['settings']['max_videos_per_channel']
        
        try:
            # Try multiple URL formats for better compatibility
            urls_to_try = [
                channel_url.rstrip('/') + '/videos',
                channel_url.rstrip('/') + '/streams', 
                channel_url.rstrip('/'),
            ]
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
            }
            
            found_video_ids = set()
            
            for url in urls_to_try:
                if len(found_video_ids) >= max_videos:
                    break
                    
                try:
                    if self.verbose:
                        print(f"  Trying URL: {url}")
                    
                    response = self.session.get(url, headers=headers, timeout=30)
                    response.raise_for_status()
                    
                    # Multiple approaches to extract video IDs
                    
                    # 1. Look for video IDs in JavaScript/JSON
                    video_patterns = [
                        r'"videoId":"([a-zA-Z0-9_-]{11})"',
                        r'/watch\?v=([a-zA-Z0-9_-]{11})',
                        r'"url":"/watch\?v=([a-zA-Z0-9_-]{11})"',
                        r'videoRenderer":\{[^}]*"videoId":"([a-zA-Z0-9_-]{11})"',
                        r'"watchEndpoint":\{"videoId":"([a-zA-Z0-9_-]{11})"',
                    ]
                    
                    for pattern in video_patterns:
                        matches = re.findall(pattern, response.text)
                        found_video_ids.update(matches)
                        if len(found_video_ids) >= max_videos:
                            break
                    
                    # 2. Also look in HTML for any remaining video links
                    soup = BeautifulSoup(response.text, 'html.parser')
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        if '/watch?v=' in href:
                            match = re.search(r'v=([a-zA-Z0-9_-]{11})', href)
                            if match:
                                found_video_ids.add(match.group(1))
                                if len(found_video_ids) >= max_videos:
                                    break
                    
                    if found_video_ids:
                        if self.verbose:
                            print(f"  Found {len(found_video_ids)} video IDs from {url}")
                        break
                        
                except Exception as e:
                    if self.verbose:
                        print(f"  Failed to scrape {url}: {e}")
                    continue
            
            # Convert video IDs to full video info
            for video_id in list(found_video_ids)[:max_videos]:
                video_url = f"https://youtube.com/watch?v={video_id}"
                videos.append({
                    'url': video_url,
                    'title': None,  # Will be fetched when processing
                    'published': None
                })
                
        except Exception as e:
            if self.verbose:
                print(f"⚠️  Channel scraping failed: {e}")
        
        return videos
    
    def get_channel_videos(self, channel_url: str) -> List[Dict]:
        """Get videos from a YouTube channel using API or scraping"""
        if self.verbose:
            print(f"🔍 Discovering videos from channel: {channel_url}")
        
        # Try API first
        videos = self.get_channel_videos_api(channel_url)
        
        # Fall back to scraping if API failed
        if not videos:
            videos = self.get_channel_videos_scraping(channel_url)
        
        if self.verbose:
            print(f"📺 Found {len(videos)} videos from channel")
        
        return videos


class DomainScraper:
    """Website domain scraping functionality"""
    
    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose
        self.session = requests.Session()
        self.visited_urls = set()
        
        # Common blog/article URL patterns
        self.article_patterns = [
            r'/blog/',
            r'/article/',
            r'/post/',
            r'/news/',
            r'/\d{4}/\d{2}/',  # Date-based URLs like /2024/01/
        ]
        
        # URLs to avoid
        self.exclude_patterns = [
            r'/tag/',
            r'/category/',
            r'/author/',
            r'/search',
            r'/wp-admin/',
            r'/wp-content/',
            r'\.jpg$|\.png$|\.gif$|\.pdf$|\.zip$',
        ]
    
    def normalize_url(self, url: str, base_url: str) -> str:
        """Normalize and make URL absolute"""
        if url.startswith('//'):
            url = 'https:' + url
        elif url.startswith('/'):
            from urllib.parse import urljoin
            url = urljoin(base_url, url)
        elif not url.startswith(('http://', 'https://')):
            from urllib.parse import urljoin
            url = urljoin(base_url, url)
        return url
    
    def is_article_url(self, url: str) -> bool:
        """Check if URL looks like an article/blog post"""
        # Check if it matches article patterns
        for pattern in self.article_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        
        # Check if it should be excluded
        for pattern in self.exclude_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return False
        
        # Additional heuristics - if URL has more path segments, likely an article
        path_segments = url.split('/')
        return len(path_segments) > 4
    
    def get_sitemap_urls(self, domain: str) -> List[str]:
        """Extract URLs from sitemap.xml"""
        urls = []
        sitemap_urls = [
            f"{domain}/sitemap.xml",
            f"{domain}/sitemap_index.xml",
            f"{domain}/post-sitemap.xml",
            f"{domain}/blog-sitemap.xml",
        ]
        
        for sitemap_url in sitemap_urls:
            try:
                if self.verbose:
                    print(f"  Checking sitemap: {sitemap_url}")
                
                response = self.session.get(sitemap_url, timeout=10)
                if response.status_code == 200:
                    try:
                        soup = BeautifulSoup(response.text, 'xml')
                    except:
                        soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Look for URLs in sitemap
                    for loc in soup.find_all('loc'):
                        url = loc.get_text().strip()
                        if self.is_article_url(url):
                            urls.append(url)
                            
                    if self.verbose:
                        print(f"  Found {len(urls)} URLs in sitemap")
                        
                    if urls:  # If we found URLs, don't try other sitemaps
                        break
                        
            except Exception as e:
                if self.verbose:
                    print(f"  Sitemap {sitemap_url} failed: {e}")
                continue
        
        return urls[:self.config.data['settings']['max_posts_per_domain']]
    
    def get_rss_urls(self, domain: str) -> List[str]:
        """Extract URLs from RSS feeds"""
        urls = []
        rss_urls = [
            f"{domain}/feed",
            f"{domain}/rss.xml",
            f"{domain}/feed.xml",
            f"{domain}/blog/feed",
            f"{domain}/news/feed",
        ]
        
        for rss_url in rss_urls:
            try:
                if self.verbose:
                    print(f"  Checking RSS feed: {rss_url}")
                
                response = self.session.get(rss_url, timeout=10)
                if response.status_code == 200:
                    try:
                        soup = BeautifulSoup(response.text, 'xml')
                    except:
                        soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Look for URLs in RSS items
                    for item in soup.find_all('item'):
                        link = item.find('link')
                        if link:
                            url = link.get_text().strip()
                            if self.is_article_url(url):
                                urls.append(url)
                    
                    # Also check for Atom feeds
                    for entry in soup.find_all('entry'):
                        link = entry.find('link')
                        if link and link.get('href'):
                            url = link['href']
                            if self.is_article_url(url):
                                urls.append(url)
                    
                    if self.verbose:
                        print(f"  Found {len(urls)} URLs in RSS feed")
                    
                    if urls:  # If we found URLs, don't try other feeds
                        break
                        
            except Exception as e:
                if self.verbose:
                    print(f"  RSS feed {rss_url} failed: {e}")
                continue
        
        return urls[:self.config.data['settings']['max_posts_per_domain']]
    
    def crawl_domain(self, domain: str, current_depth: int = 0) -> List[str]:
        """Crawl domain for article URLs"""
        if current_depth >= self.config.data['settings']['crawl_depth']:
            return []
        
        urls = []
        max_posts = self.config.data['settings']['max_posts_per_domain']
        
        try:
            if self.verbose and current_depth == 0:
                print(f"  Crawling domain: {domain}")
            
            response = self.session.get(domain, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract all internal links
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = self.normalize_url(href, domain)
                
                # Skip if already visited or external
                if full_url in self.visited_urls:
                    continue
                    
                if not full_url.startswith(domain):
                    continue
                
                self.visited_urls.add(full_url)
                
                # Check if this looks like an article
                if self.is_article_url(full_url):
                    urls.append(full_url)
                    if len(urls) >= max_posts:
                        break
            
            # If we haven't found enough and depth allows, crawl some promising links
            if len(urls) < max_posts // 2 and current_depth < self.config.data['settings']['crawl_depth'] - 1:
                promising_links = [url for url in self.visited_urls if '/blog' in url or '/news' in url][:3]
                for link in promising_links:
                    sub_urls = self.crawl_domain(link, current_depth + 1)
                    urls.extend(sub_urls)
                    if len(urls) >= max_posts:
                        break
            
        except Exception as e:
            if self.verbose:
                print(f"  Domain crawling failed: {e}")
        
        return urls[:max_posts]
    
    def get_domain_posts(self, domain: str) -> List[Dict]:
        """Get blog posts from a domain using multiple methods"""
        if self.verbose:
            print(f"🔍 Discovering posts from domain: {domain}")
        
        # Ensure domain has protocol
        if not domain.startswith(('http://', 'https://')):
            domain = 'https://' + domain
        
        urls = []
        
        # Try sitemap first (most reliable)
        sitemap_urls = self.get_sitemap_urls(domain)
        if sitemap_urls:
            urls.extend(sitemap_urls)
            if self.verbose:
                print(f"  Sitemap method: {len(sitemap_urls)} URLs")
        
        # Try RSS feeds if sitemap didn't yield enough
        if len(urls) < self.config.data['settings']['max_posts_per_domain'] // 2:
            rss_urls = self.get_rss_urls(domain)
            urls.extend([url for url in rss_urls if url not in urls])
            if self.verbose and rss_urls:
                print(f"  RSS method: {len(rss_urls)} URLs")
        
        # Try crawling if other methods didn't yield enough
        if len(urls) < self.config.data['settings']['max_posts_per_domain'] // 2:
            crawl_urls = self.crawl_domain(domain)
            urls.extend([url for url in crawl_urls if url not in urls])
            if self.verbose and crawl_urls:
                print(f"  Crawling method: {len(crawl_urls)} URLs")
        
        # Convert URLs to the format expected by the main processor
        posts = []
        for url in urls[:self.config.data['settings']['max_posts_per_domain']]:
            posts.append({
                'url': url,
                'title': None  # Will be fetched when processing
            })
        
        if self.verbose:
            print(f"📝 Found {len(posts)} posts from domain")
        
        return posts


class URLDiscovery:
    """Orchestrator for discovering URLs from channels and domains"""
    
    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose
        self.channel_scraper = ChannelScraper(config, verbose)
        self.domain_scraper = DomainScraper(config, verbose)
    
    def discover_all_sources(self) -> Tuple[List[Dict], List[Dict]]:
        """Discover videos from channels and posts from domains"""
        discovered_videos = []
        discovered_posts = []
        
        # Process YouTube channels
        channels = self.config.data['sources'].get('youtube_channels', [])
        for channel in channels:
            channel_url = channel.get('url') or channel.get('channel_id')
            if not channel_url:
                continue
                
            videos = self.channel_scraper.get_channel_videos(channel_url)
            discovered_videos.extend(videos)
        
        # Process website domains  
        domains = self.config.data['sources'].get('website_domains', [])
        for domain in domains:
            domain_url = domain.get('url') or domain.get('domain')
            if not domain_url:
                continue
                
            posts = self.domain_scraper.get_domain_posts(domain_url)
            discovered_posts.extend(posts)
        
        return discovered_videos, discovered_posts


class LinkChecker:
    """Main link checking functionality"""
    
    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose
        self.ua = UserAgent()
        self.session = requests.Session()
        
        # Anti-bot user agents
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        ]
        
        # Initialize YouTube API if available
        self.youtube_service = None
        if (YOUTUBE_API_AVAILABLE and 
            self.config.data['settings'].get('youtube_api_key')):
            try:
                self.youtube_service = build(
                    'youtube', 'v3',
                    developerKey=self.config.data['settings']['youtube_api_key']
                )
            except Exception as e:
                if self.verbose:
                    print(f"⚠️  YouTube API initialization failed: {e}")
                    print("Falling back to web scraping for YouTube videos")
    
    def get_headers(self) -> dict:
        """Generate headers with rotating user agent"""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-GB,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from URL"""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    def extract_affiliate_links(self, text: str) -> List[Dict[str, str]]:
        """Extract affiliate links from text content"""
        links = []
        
        # Amazon patterns (UK and US)
        amazon_patterns = [
            r'(https?://(?:www\.)?amazon\.co\.uk/[^\s]+)',
            r'(https?://(?:www\.)?amazon\.com/[^\s]+)', 
            r'(https?://amzn\.to/[a-zA-Z0-9]+)',
        ]
        
        # AliExpress patterns  
        aliexpress_patterns = [
            r'(https?://(?:www\.)?aliexpress\.com/[^\s]+)',
            r'(https?://s\.click\.aliexpress\.com/e/_[a-zA-Z0-9]+)',
        ]
        
        all_patterns = amazon_patterns + aliexpress_patterns
        
        for pattern in all_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                url = match.strip()
                platform = 'amazon' if 'amazon' in url or 'amzn' in url else 'aliexpress'
                links.append({
                    'url': url,
                    'platform': platform,
                    'title': self._extract_link_title_from_context(text, url)
                })
        
        return links
    
    def _extract_link_title_from_context(self, text: str, url: str) -> str:
        """Try to extract link title from surrounding context"""
        lines = text.split('\n')
        for line in lines:
            if url in line:
                cleaned = re.sub(r'https?://[^\s]+', '', line).strip()
                if cleaned and len(cleaned) > 5:
                    return cleaned[:50]
        return "Link"
    
    def get_youtube_content(self, video_url: str, title: str = None) -> Dict:
        """Get YouTube video content using API or scraping"""
        video_id = self.extract_video_id(video_url)
        if not video_id:
            return {
                'title': title or 'Unknown Video',
                'description': '',
                'error': 'Invalid YouTube URL'
            }
        
        # Try API first if available
        if self.youtube_service:
            try:
                response = self.youtube_service.videos().list(
                    part='snippet',
                    id=video_id
                ).execute()
                
                if response['items']:
                    item = response['items'][0]['snippet']
                    return {
                        'title': title or item['title'],
                        'description': item.get('description', ''),
                        'error': None
                    }
            except Exception as e:
                if self.verbose:
                    print(f"⚠️  YouTube API failed for {video_id}: {e}")
        
        # Fall back to scraping
        return self._scrape_youtube_video(video_url, title)
    
    def _scrape_youtube_video(self, video_url: str, title: str = None) -> Dict:
        """Scrape YouTube video page for description"""
        try:
            response = self.session.get(
                video_url, 
                headers=self.get_headers(),
                timeout=self.config.data['settings']['request_timeout']
            )
            response.raise_for_status()
            
            # Extract description from page HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try to get title if not provided
            if not title:
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.get_text().replace(' - YouTube', '')
            
            # Look for description in various script tags
            description = ""
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and 'shortDescription' in script.string:
                    # Try to extract description from JSON
                    try:
                        content = script.string
                        desc_match = re.search(r'"shortDescription":"([^"]*)"', content)
                        if desc_match:
                            description = desc_match.group(1)
                            description = description.encode().decode('unicode_escape')
                            break
                    except:
                        continue
            
            return {
                'title': title or 'YouTube Video',
                'description': description,
                'error': None
            }
            
        except Exception as e:
            return {
                'title': title or 'YouTube Video',
                'description': '',
                'error': f'Failed to fetch video: {e}'
            }
    
    def get_blog_content(self, blog_url: str, title: str = None) -> Dict:
        """Get blog post content"""
        try:
            response = self.session.get(
                blog_url,
                headers=self.get_headers(), 
                timeout=self.config.data['settings']['request_timeout']
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Get title if not provided - try multiple methods
            if not title:
                # Method 1: <title> tag
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.get_text().strip()
                    # Clean up HTML entities
                    import html
                    title = html.unescape(title)
                
                # Method 2: Try Open Graph title if no title or generic title
                if not title or 'Blog Post' in title or len(title) < 3:
                    og_title = soup.find('meta', property='og:title')
                    if og_title and og_title.get('content'):
                        title = og_title['content'].strip()
                
                # Method 3: Try h1 tag
                if not title or 'Blog Post' in title or len(title) < 3:
                    h1_tag = soup.find('h1')
                    if h1_tag:
                        title = h1_tag.get_text().strip()
            
            # Debug output
            if self.verbose and not title:
                print(f"    ⚠️  Could not extract title from {blog_url}")
            
            # Extract all text content
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            text = soup.get_text()
            
            # Also get all links with their anchor text
            links_html = []
            for link in soup.find_all('a', href=True):
                href = link['href']
                text_content = link.get_text().strip()
                if text_content:
                    links_html.append(f"{text_content}: {href}")
            
            full_content = text + "\n" + "\n".join(links_html)
            
            return {
                'title': title or 'Blog Post',
                'content': full_content,
                'error': None
            }
            
        except Exception as e:
            if self.verbose:
                print(f"    ⚠️  Failed to fetch blog post {blog_url}: {e}")
            return {
                'title': title or 'Blog Post',
                'content': '',
                'error': f'Failed to fetch blog post: {e}'
            }
    
    def check_amazon_link(self, url: str) -> Dict:
        """Check Amazon UK link status"""
        try:
            # Add delay for rate limiting
            time.sleep(self.config.data['settings']['delay_between_requests'])
            
            response = self.session.get(
                url,
                headers=self.get_headers(),
                timeout=self.config.data['settings']['request_timeout'],
                allow_redirects=True
            )
            
            # Handle Amazon's 500 errors - if we got redirected to a product page, 
            # it's likely the link works even if Amazon returns 500
            if response.status_code == 500 and '/dp/' in response.url:
                return {
                    'status': 'working',
                    'title': 'Amazon Product (500 Error but Valid Product Page)',
                    'price': 'Price check failed (500 error)',
                    'error': None
                }
            
            # Handle other HTTP errors
            if response.status_code != 200:
                return {
                    'status': 'broken',
                    'title': 'Amazon Product',
                    'price': None,
                    'error': f'HTTP {response.status_code} error'
                }
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check if redirected to search page (product removed)
            if '/s?' in response.url or 'search' in response.url.lower():
                return {
                    'status': 'broken',
                    'title': 'Product Not Found',
                    'price': None,
                    'error': 'Product no longer available (redirects to search page)'
                }
            
            # Extract product title
            title_selectors = ['#productTitle', 'h1.a-size-large', 'h1 span']
            title = 'Amazon Product'
            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    title = title_elem.get_text().strip()
                    break
            
            # Extract price
            price = None
            price_selectors = [
                '.a-price-whole',
                '.a-offscreen',
                '.a-price .a-offscreen',
                '#price_inside_buybox'
            ]
            for selector in price_selectors:
                price_elem = soup.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text().strip()
                    if '£' in price_text:
                        price = price_text
                        break
            
            # Check availability
            availability_indicators = [
                'Currently unavailable',
                'Out of stock',
                'Temporarily out of stock'
            ]
            
            page_text = soup.get_text().lower()
            for indicator in availability_indicators:
                if indicator.lower() in page_text:
                    return {
                        'status': 'broken',
                        'title': title,
                        'price': price,
                        'error': f'Product {indicator.lower()}'
                    }
            
            # If we got here, assume it's working
            return {
                'status': 'working',
                'title': title,
                'price': price or 'Price not found',
                'error': None
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'status': 'broken',
                'title': 'Amazon Product',
                'price': None,
                'error': f'Network error: {e}'
            }
        except Exception as e:
            return {
                'status': 'broken',
                'title': 'Amazon Product', 
                'price': None,
                'error': f'Check failed: {e}'
            }
    
    def check_aliexpress_link(self, url: str) -> Dict:
        """Check AliExpress link status"""
        try:
            # Add delay for rate limiting
            time.sleep(self.config.data['settings']['delay_between_requests'])
            
            response = self.session.get(
                url,
                headers=self.get_headers(),
                timeout=self.config.data['settings']['request_timeout'],
                allow_redirects=True
            )
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Debug info for verbose mode
            if self.verbose:
                final_url = response.url
                if final_url != url:
                    print(f"      → Redirected to: {final_url[:60]}...")
            
            # Check for error pages
            if response.status_code == 404:
                return {
                    'status': 'broken',
                    'title': 'AliExpress Product',
                    'price': None,
                    'error': '404 Not Found'
                }
            
            # Extract product title - try multiple approaches
            title = 'AliExpress Product'
            
            # First try OpenGraph meta tags (most reliable for AliExpress)
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                og_content = og_title['content'].strip()
                # Remove AliExpress suffixes
                for suffix in [' - AliExpress', ' | AliExpress', ' on AliExpress', ' - AliExpress 13']:
                    if suffix in og_content:
                        title = og_content.split(suffix)[0].strip()
                        break
                else:
                    if len(og_content) > 10:
                        title = og_content[:80].strip()
            
            # If no og:title, try CSS selectors
            if title == 'AliExpress Product':
                title_selectors = [
                    'h1[data-pl="product-title"]',
                    '.product-title-text',
                    'h1.product-title',
                    '.pdp-product-title',
                    '.product-title',
                    'h1',  # fallback to any h1
                    '[data-spm-anchor-id*="title"]',
                    '.title-text'
                ]
                
                for selector in title_selectors:
                    title_elem = soup.select_one(selector)
                    if title_elem:
                        candidate = title_elem.get_text().strip()
                        if candidate and len(candidate) > 5 and 'aliexpress' not in candidate.lower():
                            title = candidate
                            break
            
            # If still no title found, try extracting from page title
            if title == 'AliExpress Product':
                page_title = soup.find('title')
                if page_title:
                    page_title_text = page_title.get_text()
                    # Remove common suffixes
                    for suffix in [' - AliExpress', ' | AliExpress', ' on AliExpress']:
                        if suffix in page_title_text:
                            title = page_title_text.split(suffix)[0].strip()
                            break
                    else:
                        if len(page_title_text) > 10:
                            title = page_title_text[:60].strip()
            
            # Extract price - try multiple approaches
            price = None
            
            # First try OpenGraph/meta tags for price
            price_meta_tags = [
                ('meta', 'property', 'product:price:amount'),
                ('meta', 'property', 'og:price:amount'),
                ('meta', 'name', 'price'),
                ('meta', 'itemprop', 'price')
            ]
            
            for tag, attr, value in price_meta_tags:
                price_meta = soup.find(tag, {attr: value})
                if price_meta and price_meta.get('content'):
                    price_content = price_meta['content'].strip()
                    if price_content and price_content != '0':
                        # Get currency if available
                        currency_meta = soup.find('meta', property='product:price:currency')
                        currency = currency_meta['content'] if currency_meta else ''
                        price = f"{currency} {price_content}".strip()
                        break
            
            # If no meta price, try CSS selectors
            if not price:
                price_selectors = [
                    '.product-price-current',
                    '.price-current', 
                    '.pdp-price',
                    '[data-spm-anchor-id*="price"]',
                    '.price-value',
                    '.price',
                    'span[class*="price"]'
                ]
                
                for selector in price_selectors:
                    price_elem = soup.select_one(selector)
                    if price_elem:
                        candidate = price_elem.get_text().strip()
                        # Look for currency symbols
                        if any(symbol in candidate for symbol in ['$', '€', '£', '¥', 'US', 'EUR']):
                            price = candidate
                            break
            
            # Also try to find price in script tags (JSON data)
            if not price:
                script_tags = soup.find_all('script')
                for script in script_tags:
                    if script.string and any(keyword in script.string for keyword in ['price', 'amount']):
                        # Look for price patterns in JSON
                        price_matches = re.findall(r'["\']price["\']:\s*["\']?([^"\',$\s]+)', script.string)
                        if price_matches:
                            candidate = price_matches[0]
                            if candidate and candidate != '0':
                                price = candidate
                                break
            
            # Check if product exists by looking for common error indicators
            page_text = soup.get_text().lower()
            error_indicators = [
                'product not found',
                'item not available',
                'seller not found'
            ]
            
            for indicator in error_indicators:
                if indicator in page_text:
                    return {
                        'status': 'broken',
                        'title': title,
                        'price': price,
                        'error': f'Product {indicator}'
                    }
            
            # If we got a reasonable response, assume it's working
            return {
                'status': 'working',
                'title': title,
                'price': price or 'Price not found',
                'error': None
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'status': 'broken',
                'title': 'AliExpress Product',
                'price': None,
                'error': f'Network error: {e}'
            }
        except Exception as e:
            return {
                'status': 'broken',
                'title': 'AliExpress Product',
                'price': None, 
                'error': f'Check failed: {e}'
            }
    
    def check_link(self, link_info: Dict) -> Dict:
        """Check a single link and return detailed results"""
        url = link_info['url']
        platform = link_info['platform']
        
        if platform == 'amazon':
            result = self.check_amazon_link(url)
        elif platform == 'aliexpress':
            result = self.check_aliexpress_link(url)
        else:
            result = {
                'status': 'broken',
                'title': link_info['title'],
                'price': None,
                'error': f'Unsupported platform: {platform}'
            }
        
        # Add original link info
        result.update({
            'url': url,
            'platform': platform,
            'original_title': link_info['title'],
            'source': link_info['source']
        })
        
        return result
    
    def process_sources(self, discover_mode: bool = False) -> Tuple[List[Dict], List[Dict]]:
        """Process all sources and extract links"""
        all_sources = []
        all_links = []
        
        # Handle URL discovery from channels and domains
        if discover_mode:
            if self.verbose:
                print("🔍 DISCOVERING URLS FROM CHANNELS AND DOMAINS...")
            
            discovery = URLDiscovery(self.config, self.verbose)
            discovered_videos, discovered_posts = discovery.discover_all_sources()
            
            # Add discovered videos to sources
            if discovered_videos:
                if self.verbose:
                    print(f"📺 Discovered {len(discovered_videos)} videos from channels")
                # Merge with existing videos
                existing_videos = self.config.data['sources'].get('youtube_videos', []) or []
                all_videos = existing_videos + discovered_videos
            else:
                all_videos = self.config.data['sources'].get('youtube_videos', []) or []
            
            # Add discovered posts to sources  
            if discovered_posts:
                if self.verbose:
                    print(f"📝 Discovered {len(discovered_posts)} posts from domains")
                # Merge with existing posts
                existing_posts = self.config.data['sources'].get('blog_posts', []) or []
                all_posts = existing_posts + discovered_posts
            else:
                all_posts = self.config.data['sources'].get('blog_posts', []) or []
        else:
            # Use existing configured sources
            all_videos = self.config.data['sources'].get('youtube_videos', []) or []
            all_posts = self.config.data['sources'].get('blog_posts', []) or []
        
        # Process YouTube videos
        for video in all_videos:
            if self.verbose:
                print(f"📺 Processing: {video.get('title', video['url'])}")
            
            content = self.get_youtube_content(video['url'], video.get('title'))
            links = self.extract_affiliate_links(content['description'])
            
            source_info = {
                'type': 'youtube',
                'url': video['url'], 
                'title': content['title'],
                'links': links,
                'error': content['error']
            }
            all_sources.append(source_info)
            
            # Add source context to each link
            for link in links:
                link['source'] = source_info
                all_links.append(link)
        
        # Process blog posts
        for blog in all_posts:
            # First get the content to extract the title
            content = self.get_blog_content(blog['url'], blog.get('title'))
            
            # Now we can show the proper title
            if self.verbose:
                display_title = content['title'] if content['title'] != 'Blog Post' else blog.get('title', blog['url'])
                print(f"📝 Processing: {display_title}")
            
            links = self.extract_affiliate_links(content['content'])
            
            source_info = {
                'type': 'blog',
                'url': blog['url'],
                'title': content['title'], 
                'links': links,
                'error': content['error']
            }
            all_sources.append(source_info)
            
            # Add source context to each link
            for link in links:
                link['source'] = source_info
                all_links.append(link)
        
        return all_sources, all_links
    
    def check_all_links(self, links: List[Dict]) -> List[Dict]:
        """Check all links with concurrent processing"""
        if not links:
            return []
        
        results = []
        max_workers = self.config.data['settings']['concurrent_requests']
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all link checking tasks
            future_to_link = {
                executor.submit(self.check_link, link): link 
                for link in links
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_link):
                result = future.result()
                results.append(result)
                
                if self.verbose:
                    status_icon = "✅" if result['status'] == 'working' else "❌"
                    source_type = "📺" if result['source']['type'] == 'youtube' else "📝"
                    source_title = result['source']['title'][:30]
                    link_url = result['url'][:60]
                    product_title = result['title'][:40]
                    print(f"  {status_icon} {source_type} {source_title} | {product_title}")
                    print(f"      └─ {link_url}")
        
        return results


class OutputFormatter:
    """Handle different output formats"""
    
    def __init__(self, verbose: bool = False, format_type: str = 'text'):
        self.verbose = verbose
        self.format_type = format_type
    
    def format_results(self, sources: List[Dict], results: List[Dict]) -> str:
        """Format results based on specified format"""
        if self.format_type == 'json':
            return self._format_json(results)
        else:
            return self._format_text(sources, results)
    
    def _format_json(self, results: List[Dict]) -> str:
        """Format results as JSON"""
        working_count = sum(1 for r in results if r['status'] == 'working')
        broken_count = len(results) - working_count
        
        issues = []
        for result in results:
            if result['status'] == 'broken':
                issues.append({
                    'source_type': result['source']['type'],
                    'source_title': result['source']['title'],
                    'source_url': result['source']['url'],
                    'link_url': result['url'],
                    'link_title': result['title'],
                    'status': result['status'],
                    'error': result['error'],
                    'platform': result['platform']
                })
        
        output = {
            'summary': {
                'total_links': len(results),
                'working': working_count,
                'broken': broken_count,
                'check_time': datetime.now().isoformat()
            },
            'issues': issues
        }
        
        return json.dumps(output, indent=2)
    
    def _format_text(self, sources: List[Dict], results: List[Dict]) -> str:
        """Format results as human-readable text"""
        if not results:
            return "No affiliate links found in the provided sources."
        
        working_results = [r for r in results if r['status'] == 'working']
        broken_results = [r for r in results if r['status'] == 'broken']
        
        output_lines = []
        
        # Header with summary
        if broken_results:
            output_lines.append(f"🚨 BROKEN LINKS FOUND ({len(broken_results)} issues)")
            output_lines.append("")
        
        # Show broken links (always shown)
        if broken_results:
            output_lines.append("❌ BROKEN LINKS:")
            
            # Group by source
            sources_with_broken = {}
            for result in broken_results:
                source_key = (result['source']['type'], result['source']['title'])
                if source_key not in sources_with_broken:
                    sources_with_broken[source_key] = []
                sources_with_broken[source_key].append(result)
            
            for (source_type, source_title), source_results in sources_with_broken.items():
                icon = "📺" if source_type == 'youtube' else "📝"
                output_lines.append(f"{icon} \"{source_title}\"")
                
                for result in source_results:
                    title = result['title'] if result['title'] != 'Link' else result['original_title']
                    output_lines.append(f"  ├─ {title} - {result['url']}")
                    output_lines.append(f"     └─ ERROR: {result['error']}")
                output_lines.append("")
        
        # Show working links only in verbose mode
        if self.verbose and working_results:
            output_lines.append("✅ WORKING LINKS:")
            
            # Group by source
            sources_with_working = {}
            for result in working_results:
                source_key = (result['source']['type'], result['source']['title'])
                if source_key not in sources_with_working:
                    sources_with_working[source_key] = []
                sources_with_working[source_key].append(result)
            
            for (source_type, source_title), source_results in sources_with_working.items():
                icon = "📺" if source_type == 'youtube' else "📝"
                output_lines.append(f"{icon} \"{source_title}\"")
                
                for result in source_results:
                    title = result['title'] if result['title'] != 'Link' else result['original_title']
                    price = f" - {result['price']}" if result['price'] else ""
                    output_lines.append(f"  └─ ✅ {title}{price}")
                output_lines.append("")
        
        # Summary
        if not broken_results:
            output_lines.append(f"✅ All links are working properly ({len(working_results)} links checked)")
        else:
            output_lines.append(f"📊 SUMMARY: {len(working_results)} working, {len(broken_results)} broken")
        
        return "\n".join(output_lines)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='LinkPulse - Check affiliate links in YouTube videos and blog posts'
    )
    parser.add_argument(
        '--config', 
        default='config.yaml',
        help='Path to YAML configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show detailed output including working links'
    )
    parser.add_argument(
        '--format',
        choices=['text', 'json'],
        default='text', 
        help='Output format (default: text)'
    )
    parser.add_argument(
        '--discover', '-d',
        action='store_true',
        help='Auto-discover URLs from YouTube channels and website domains'
    )
    parser.add_argument(
        '--discover-only',
        action='store_true',
        help='Only discover URLs, don\'t check affiliate links'
    )
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        config = Config(args.config)
        
        # Initialize checker and formatter
        checker = LinkChecker(config, args.verbose)
        formatter = OutputFormatter(args.verbose, args.format)
        
        # Determine if we should use discovery mode
        use_discovery = args.discover or args.discover_only
        
        # Process sources and extract links
        if args.verbose:
            if use_discovery:
                print("🔍 EXTRACTING LINKS FROM SOURCES (WITH AUTO-DISCOVERY)...")
            else:
                print("🔍 EXTRACTING LINKS FROM SOURCES...")
        
        sources, links = checker.process_sources(discover_mode=use_discovery)
        
        # Handle discover-only mode
        if args.discover_only:
            if not sources:
                print("No sources found from channels or domains.")
                return
            
            print(f"📊 DISCOVERY RESULTS:")
            print(f"Found {len(sources)} total sources")
            
            # Group by type for reporting
            youtube_sources = [s for s in sources if s['type'] == 'youtube']
            blog_sources = [s for s in sources if s['type'] == 'blog']
            
            if youtube_sources:
                print(f"📺 YouTube videos: {len(youtube_sources)}")
                if args.verbose:
                    for source in youtube_sources[:10]:  # Show first 10
                        print(f"  • {source['title'][:60]} - {source['url']}")
                    if len(youtube_sources) > 10:
                        print(f"  ... and {len(youtube_sources) - 10} more")
            
            if blog_sources:
                print(f"📝 Blog posts: {len(blog_sources)}")
                if args.verbose:
                    for source in blog_sources[:10]:  # Show first 10
                        print(f"  • {source['title'][:60]} - {source['url']}")
                    if len(blog_sources) > 10:
                        print(f"  ... and {len(blog_sources) - 10} more")
            
            total_links = sum(len(s['links']) for s in sources)
            print(f"🔗 Total affiliate links found: {total_links}")
            return
        
        if not links:
            print("No affiliate links found in the provided sources.")
            return
        
        # Check all links
        if args.verbose:
            print(f"\n🔗 CHECKING {len(links)} AFFILIATE LINKS...")
        
        results = checker.check_all_links(links)
        
        # Output results
        if args.verbose:
            print("\n" + "="*50)
        
        output = formatter.format_results(sources, results)
        print(output)
        
        # Exit with error code if there are broken links (for scripting)
        broken_count = sum(1 for r in results if r['status'] == 'broken')
        if broken_count > 0:
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n⏹️  Stopped by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()