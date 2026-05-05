#!/usr/bin/env python3
"""
QuickBooks Online Setup Helper

Configures the .env file with Intuit OAuth credentials for QB Online sandbox.

Usage:
    python scripts/setup_qbo.py [--env-path PATH]

This script will:
1. Check if backend/.env exists
2. Add or update Intuit credentials
3. Enable QB Online sandbox environment
4. Verify OAuth redirect URI
"""

import os
import sys
import argparse
from pathlib import Path

def load_env_file(env_path: str) -> dict:
    """Load .env file as key-value pairs."""
    env_vars = {}
    env_file = Path(env_path)
    
    if env_file.exists():
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    
    return env_vars

def save_env_file(env_path: str, env_vars: dict):
    """Save key-value pairs back to .env file, preserving comments."""
    env_file = Path(env_path)
    
    # Read original file to preserve comments and structure
    original_lines = []
    if env_file.exists():
        with open(env_file, 'r') as f:
            original_lines = f.readlines()
    
    # Build new content
    new_content = []
    keys_updated = set()
    
    for line in original_lines:
        stripped = line.strip()
        
        # Keep comments and empty lines
        if not stripped or stripped.startswith('#'):
            new_content.append(line)
            continue
        
        # Check if this is a key we need to update
        if '=' in line:
            key = stripped.split('=', 1)[0].strip()
            if key in env_vars:
                new_content.append(f"{key}={env_vars[key]}\n")
                keys_updated.add(key)
            else:
                new_content.append(line)
        else:
            new_content.append(line)
    
    # Add any new keys that weren't in the original file
    for key, value in env_vars.items():
        if key not in keys_updated:
            new_content.append(f"{key}={value}\n")
    
    # Write back
    with open(env_file, 'w') as f:
        f.writelines(new_content)

def setup_qbo(env_path: str = "backend/.env"):
    """Set up QB Online credentials in .env file."""
    
    env_file = Path(env_path)
    
    # Create .env if it doesn't exist
    if not env_file.exists():
        print(f"❌ {env_path} not found.")
        print("   Create it from backend/.env.example:")
        print(f"   cp backend/.env.example {env_path}")
        print("   Then run this script again.")
        return False
    
    print(f"📝 Configuring {env_path}...")
    
    # Load current env vars
    env_vars = load_env_file(env_path)
    
    # Intuit QB Online credentials
    intuit_creds = {
        "INTUIT_CLIENT_ID": "ABuH2jRPl2YGs8kgbLjZiUtYf6hPruUUGKceK3pDZ4LBSoDANS",
        "INTUIT_CLIENT_SECRET": "P40BQz5o53KzdzAfzAbve9DUKK7rYqUj0JMwVOZI",
        "INTUIT_ENVIRONMENT": "sandbox",
        "INTUIT_REDIRECT_URI": "http://localhost:8000/auth/qbo/callback",
    }
    
    # Update or add credentials
    updated = []
    for key, value in intuit_creds.items():
        old_value = env_vars.get(key)
        env_vars[key] = value
        
        if old_value and old_value != value:
            updated.append(f"  ✓ {key}: updated")
        elif old_value:
            updated.append(f"  ✓ {key}: already set")
        else:
            updated.append(f"  + {key}: added")
    
    # Save updated env file
    save_env_file(env_path, env_vars)
    
    print("\n✅ QB Online Configuration Complete!\n")
    print("Updated settings:")
    for item in updated:
        print(item)
    
    print("\n" + "="*60)
    print("NEXT STEPS:")
    print("="*60)
    print("""
1. Verify your Intuit app is registered:
   https://developer.intuit.com/app/developer/myapps
   
2. Add this redirect URI to your Intuit app:
   Settings → Keys & OAuth → Redirect URIs
   http://localhost:8000/auth/qbo/callback

3. Start the SecretaryAI backend:
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload

4. Start the frontend:
   cd frontend
   npm install
   npm run dev

5. Go to http://localhost:5173 and:
   Settings → Integrations → QuickBooks Online → Connect

6. You'll be redirected to Intuit OAuth.
   Log in and authorize access to your sandbox.

7. Done! Your QB Online sandbox is connected.
""")
    
    print("="*60)
    print("Documentation: QB_ONLINE_SETUP.md")
    print("="*60)
    
    return True

def verify_setup(env_path: str = "backend/.env"):
    """Verify QB Online is configured."""
    env_vars = load_env_file(env_path)
    
    required = {
        "INTUIT_CLIENT_ID": "ABuH2jRPl2YGs8kgbLjZiUtYf6hPruUUGKceK3pDZ4LBSoDANS",
        "INTUIT_CLIENT_SECRET": "P40BQz5o53KzdzAfzAbve9DUKK7rYqUj0JMwVOZI",
        "INTUIT_ENVIRONMENT": "sandbox",
    }
    
    print("\n🔍 Verifying QB Online Configuration...\n")
    
    all_ok = True
    for key, expected in required.items():
        value = env_vars.get(key, "")
        if value == expected:
            print(f"  ✓ {key}: configured correctly")
        else:
            print(f"  ✗ {key}: NOT configured (expected: {expected[:20]}...)")
            all_ok = False
    
    redirect_uri = env_vars.get("INTUIT_REDIRECT_URI", "")
    if redirect_uri == "http://localhost:8000/auth/qbo/callback":
        print(f"  ✓ INTUIT_REDIRECT_URI: {redirect_uri}")
    else:
        print(f"  ⚠ INTUIT_REDIRECT_URI: {redirect_uri} (dev default)")
    
    if all_ok:
        print("\n✅ QB Online is configured and ready!")
    else:
        print("\n❌ QB Online configuration incomplete. Run setup_qbo() first.")
    
    return all_ok

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Set up QB Online credentials for SecretaryAI"
    )
    parser.add_argument(
        "--env-path",
        type=str,
        default="backend/.env",
        help="Path to .env file (default: backend/.env)"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only verify configuration, don't update"
    )
    
    args = parser.parse_args()
    
    if args.verify:
        verify_setup(args.env_path)
    else:
        success = setup_qbo(args.env_path)
        sys.exit(0 if success else 1)
