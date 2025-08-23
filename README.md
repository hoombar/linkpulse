# LinkPulse CLI

A simple Python CLI tool that reads YouTube videos and blog posts from a YAML config file, extracts their affiliate links, validates them, and outputs which links need attention.

## Features

- ✅ **YouTube video support** - Extract links from video descriptions (API + scraping)
- ✅ **Blog post support** - Extract links from any web page content
- ✅ **Amazon UK validation** - Check product availability, pricing, and stock status
- ✅ **AliExpress validation** - Verify product pages and seller status
- ✅ **Anti-bot measures** - Rate limiting, user agent rotation, proper headers
- ✅ **Concurrent processing** - Fast checking with configurable concurrency
- ✅ **Multiple output formats** - Human-readable text or JSON for automation
- ✅ **Comprehensive error handling** - Retry logic and graceful failures

## Installation

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create configuration file:**
   ```bash
   cp config.example.yaml config.yaml
   ```

3. **Edit config.yaml with your URLs:**
   ```yaml
   sources:
     youtube_videos:
       - url: "https://youtube.com/watch?v=YOUR_VIDEO_ID"
         title: "Your Video Title"
     blog_posts:
       - url: "https://yourblog.com/your-post"
         title: "Your Blog Post"
   ```

## Usage

### Basic Commands

**Check all links (show only broken ones):**
```bash
python linkpulse.py
```

**Verbose output (show working + broken links):**
```bash
python linkpulse.py --verbose
```

**Custom configuration file:**
```bash
python linkpulse.py --config my-config.yaml
```

**JSON output for automation:**
```bash
python linkpulse.py --format json
```

### Example Output

**Default output (broken links only):**
```
🚨 BROKEN LINKS FOUND (2 issues)

❌ BROKEN LINKS:
📺 "How to Build a Gaming PC 2024"
  ├─ Graphics Card - https://amazon.co.uk/dp/B1234567890
     └─ ERROR: Product no longer available (redirects to search page)

📝 "Top 10 Tech Accessories for 2024"
  ├─ AliExpress Cable Management - https://s.click.aliexpress.com/e/_ABC123
     └─ ERROR: 404 Not Found

📊 SUMMARY: 16 working, 2 broken
```

**Verbose output:**
```
🔍 EXTRACTING LINKS FROM SOURCES...
📺 Processing: How to Build a Gaming PC 2024
📝 Processing: Top 10 Tech Accessories for 2024

🔗 CHECKING 18 AFFILIATE LINKS...
  ✅ Gaming Keyboard - £89.99...
  ❌ Graphics Card...
  ✅ Gaming Mouse - £44.99...

✅ WORKING LINKS:
📺 "How to Build a Gaming PC 2024"
  └─ ✅ Gaming Keyboard - £89.99 - In Stock
  └─ ✅ Gaming Mouse - £44.99 - In Stock

❌ BROKEN LINKS:
📺 "How to Build a Gaming PC 2024" 
  ├─ Graphics Card - https://amazon.co.uk/dp/B1234567890
     └─ ERROR: Product no longer available

📊 SUMMARY: 16 working, 2 broken
```

## Configuration

### YAML Configuration Format

```yaml
sources:
  youtube_videos:
    - url: "https://youtube.com/watch?v=VIDEO_ID"
      title: "Optional video title"
    - url: "https://youtu.be/VIDEO_ID"
      # title automatically fetched if not provided
      
  blog_posts:
    - url: "https://yourblog.com/post-url"
      title: "Optional post title" 
    - url: "https://another-blog.com/article"
      # title automatically fetched

settings:
  concurrent_requests: 3        # Max simultaneous checks
  request_timeout: 30           # Request timeout (seconds)
  retry_attempts: 3            # Retry failed requests
  delay_between_requests: 1.5  # Rate limiting delay (seconds)
  youtube_api_key: "optional"  # YouTube Data API v3 key
```

### YouTube API Setup (Optional)

For better YouTube description extraction:

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create/select a project
3. Enable YouTube Data API v3
4. Create credentials (API key)
5. Add the key to your `config.yaml`

**Without API key:** Falls back to web scraping (slower but works)

### Supported Platforms

- **Amazon UK**: `amazon.co.uk`, `amzn.to` short links
- **AliExpress**: `aliexpress.com`, `s.click.aliexpress.com` tracking links

## Automation & Scripting

### Cron Job Example
```bash
# Check links daily at 9 AM, send notification if issues found
0 9 * * * cd /path/to/linkpulse && python linkpulse.py | grep -E "❌" && echo "Check your affiliate links!"
```

### JSON Output Processing
```bash
# Get broken link count
python linkpulse.py --format json | jq '.summary.broken'

# List all broken link URLs
python linkpulse.py --format json | jq '.issues[].link_url'

# Save results to file
python linkpulse.py --format json > results.json
```

### Exit Codes
- `0`: All links working
- `1`: Broken links found  
- `130`: Interrupted by user (Ctrl+C)

## Command Line Options

| Option | Description |
|--------|-------------|
| `--config FILE` | Configuration file path (default: `config.yaml`) |
| `--verbose, -v` | Show detailed output including working links |
| `--format FORMAT` | Output format: `text` (default) or `json` |

## How It Works

1. **Link Extraction**:
   - YouTube: Uses API or scrapes video page for descriptions
   - Blog posts: Fetches HTML and extracts affiliate links
   - Regex patterns match Amazon UK and AliExpress URLs

2. **Link Validation**:
   - Follows redirects to final destination
   - Amazon: Checks product pages, pricing, availability
   - AliExpress: Verifies product exists and seller active
   - Detects removed products, out of stock, 404s

3. **Anti-Bot Protection**:
   - Rotating user agents
   - Rate limiting between requests
   - Proper HTTP headers
   - Retry with exponential backoff

## Troubleshooting

**"Configuration file not found"**
```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your URLs
```

**"YouTube API failed"**
- Falls back to scraping automatically
- Add `youtube_api_key` to config for better extraction

**Rate limited / blocked**
- Increase `delay_between_requests` in config
- Reduce `concurrent_requests` 

**SSL certificate errors**
```bash
pip install --upgrade certifi requests
```

## Development

**Install in development mode:**
```bash
pip install -e .
```

**Run with debug info:**
```bash
python linkpulse.py --verbose
```

## License

MIT License - see LICENSE file for details.

## Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature-name`
3. Commit changes: `git commit -am 'Add feature'`
4. Push to branch: `git push origin feature-name`
5. Submit pull request