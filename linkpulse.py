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
        
        # Amazon UK patterns
        amazon_patterns = [
            r'(https?://(?:www\.)?amazon\.co\.uk/[^\s]+)',
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
            
            # Get title if not provided
            if not title:
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.get_text().strip()
            
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
    
    def process_sources(self) -> Tuple[List[Dict], List[Dict]]:
        """Process all sources and extract links"""
        all_sources = []
        all_links = []
        
        # Process YouTube videos
        for video in self.config.data['sources'].get('youtube_videos', []):
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
        for blog in self.config.data['sources'].get('blog_posts', []):
            if self.verbose:
                print(f"📝 Processing: {blog.get('title', blog['url'])}")
            
            content = self.get_blog_content(blog['url'], blog.get('title'))
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
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        config = Config(args.config)
        
        # Initialize checker and formatter
        checker = LinkChecker(config, args.verbose)
        formatter = OutputFormatter(args.verbose, args.format)
        
        # Process sources and extract links
        if args.verbose:
            print("🔍 EXTRACTING LINKS FROM SOURCES...")
        
        sources, links = checker.process_sources()
        
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