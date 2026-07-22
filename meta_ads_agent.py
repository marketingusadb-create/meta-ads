#!/usr/bin/env python3
"""
Universal Meta Ads Agent - Complete Desktop Application
Single file solution for automated Meta Ads campaign management
All functionality in one file - easy to run and maintain
"""

import os
import json
import time
import threading
import re
import requests
from datetime import datetime
from flask import Flask, send_from_directory, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

# ==================== SAFETY GUARD (spend caps + audit log) ====================
# This layer sits between the AI/automation and any real money being spent.
# Nothing that touches budget should bypass it.

class SafetyGuard:
    """
    Central safety layer. Two jobs:
    1. Enforce a hard, non-negotiable total daily budget cap across ALL campaigns.
       No code path -- manual, AI, or optimizer -- may exceed it.
    2. Write an append-only audit log of every budget-affecting decision,
       whether it was allowed or blocked, so nothing happens silently.
    """
    _lock = threading.Lock()

    def __init__(self, audit_log_path='audit_log.jsonl'):
        self.audit_log_path = audit_log_path
        # Hard ceiling for TOTAL daily spend across every active campaign combined.
        # Defaults to a conservative $50/day if not set. This is intentional --
        # better to force a deliberate opt-in to spend more than to guess high.
        try:
            self.max_total_daily_budget = float(os.environ.get('MAX_TOTAL_DAILY_BUDGET', 50))
        except (TypeError, ValueError):
            self.max_total_daily_budget = 50.0
        # Automatic budget increases from the optimizer are OFF by default.
        # The optimizer will still *suggest* increases, but won't apply them
        # unless this is explicitly turned on in .env.txt.
        self.auto_budget_increase_enabled = os.environ.get('AUTO_BUDGET_INCREASE_ENABLED', 'false').strip().lower() in ('1', 'true', 'yes')

    def log(self, action, allowed, reason='', **details):
        entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'action': action,
            'allowed': allowed,
            'reason': reason,
            **details
        }
        try:
            with self._lock:
                with open(self.audit_log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"[SafetyGuard] WARNING: could not write audit log: {e}")
        tag = "ALLOWED" if allowed else "BLOCKED"
        print(f"[SafetyGuard] {tag} — {action}: {reason}")
        return entry

    def current_committed_budget(self, campaigns, exclude_campaign_id=None):
        """Sum of daily_budget across all non-archived/non-trashed campaigns we know about."""
        total = 0.0
        for cid, c in (campaigns or {}).items():
            if exclude_campaign_id and cid == exclude_campaign_id:
                continue
            if c.get('status') in ('ARCHIVED', 'TRASHED'):
                continue
            b = c.get('budget_daily') or c.get('daily_budget')
            try:
                if b:
                    total += float(b)
            except (TypeError, ValueError):
                pass
        return total

    def check_new_budget(self, campaigns, proposed_daily_budget, action='create_campaign', campaign_id=None):
        """
        Returns (allowed: bool, reason: str). Call this BEFORE spending any money
        (creating a campaign, or raising a budget) -- never after.
        """
        committed = self.current_committed_budget(campaigns, exclude_campaign_id=campaign_id)
        projected_total = committed + float(proposed_daily_budget or 0)
        if projected_total > self.max_total_daily_budget:
            reason = (f"Projected total daily budget ${projected_total:.2f} would exceed the hard cap "
                      f"of ${self.max_total_daily_budget:.2f}/day (already committed: ${committed:.2f}/day)")
            self.log(action, allowed=False, reason=reason, campaign_id=campaign_id,
                     proposed_daily_budget=proposed_daily_budget, committed=committed,
                     cap=self.max_total_daily_budget)
            return False, reason
        reason = f"Within cap: ${projected_total:.2f}/day of ${self.max_total_daily_budget:.2f}/day max"
        self.log(action, allowed=True, reason=reason, campaign_id=campaign_id,
                 proposed_daily_budget=proposed_daily_budget, committed=committed,
                 cap=self.max_total_daily_budget)
        return True, reason


# Single shared instance used everywhere in this file.
safety_guard = SafetyGuard()

# ==================== CONFIG STORE (editable industry rules) ====================
# Before this, targeting/budget/objective rules per industry were hardcoded in
# three different places in the code. Now they live in one editable JSON file,
# so changing them (or adding a new industry, for a future multi-tenant version)
# doesn't require touching Python code at all.

DEFAULT_INDUSTRY_CONFIG = {
    'restaurant': {
        'primary_offering': 'Food and Dining', 'peak_hours': 'lunch and dinner',
        'target_demographics': 'families and young professionals',
        'conversion_factors': ['ambiance', 'price', 'location', 'reviews'],
        'primary_interests': ['food', 'dining', 'cooking', 'restaurants'],
        'secondary_interests': ['travel', 'entertainment', 'socializing'],
        'age_groups': ['25-44', '45-64'],
        'budget_split': {'facebook': 40, 'instagram': 30, 'messenger': 20, 'whatsapp': 10},
        'estimated_roas': 3.5,
        'objectives': ['OUTCOME_LEADS', 'OUTCOME_TRAFFIC', 'OUTCOME_SALES'],
        'ad_types': ['image carousel', 'video ads', 'collection'],
    },
    'retail': {
        'primary_offering': 'Retail Products', 'peak_hours': 'weekends and evenings',
        'target_demographics': 'all age groups',
        'conversion_factors': ['price', 'quality', 'convenience', 'brand'],
        'primary_interests': ['shopping', 'fashion', 'deals', 'products'],
        'secondary_interests': ['travel', 'entertainment', 'home improvement'],
        'age_groups': ['18-34', '35-54'],
        'budget_split': {'facebook_feed': 50, 'instagram_feed': 30, 'facebook_marketplace': 20},
        'estimated_roas': 4.2,
        'objectives': ['OUTCOME_SALES', 'OUTCOME_LEADS', 'OUTCOME_AWARENESS'],
        'ad_types': ['carousel ads', 'video ads', 'story ads'],
    },
    'service': {
        'primary_offering': 'Professional Services', 'peak_hours': 'business hours',
        'target_demographics': 'adults and businesses',
        'conversion_factors': ['reputation', 'expertise', 'cost', 'convenience'],
        'primary_interests': ['professional services', 'expertise', 'advice'],
        'secondary_interests': ['finance', 'insurance', 'real estate'],
        'age_groups': ['35-54', '55+'],
        'budget_split': {'facebook': 50, 'instagram': 30, 'messenger': 20},
        'estimated_roas': 5.1,
        'objectives': ['OUTCOME_LEADS', 'OUTCOME_TRAFFIC', 'OUTCOME_SALES'],
        'ad_types': ['image ads', 'text ads', 'video ads'],
    },
    'ecommerce': {
        'primary_offering': 'E-commerce', 'peak_hours': 'weekends and holidays',
        'target_demographics': 'tech-savvy shoppers',
        'conversion_factors': ['price', 'reviews', 'shipping', 'return policy'],
        'primary_interests': ['online shopping', 'deals', 'reviews', 'products'],
        'secondary_interests': ['technology', 'finance', 'travel'],
        'age_groups': ['18-34', '35-54'],
        'budget_split': {'facebook': 35, 'instagram': 35, 'google_ads': 30},
        'estimated_roas': 6.8,
        'objectives': ['OUTCOME_SALES', 'OUTCOME_TRAFFIC', 'OUTCOME_AWARENESS'],
        'ad_types': ['product carousel', 'video ads', 'collection ads'],
    },
    'martialarts': {
        'primary_offering': 'classes', 'peak_hours': 'afternoons and weekends',
        'target_demographics': 'adults and children',
        'conversion_factors': ['instructor quality', 'location', 'pricing', 'community'],
        'primary_interests': ['martial arts', 'self defense', 'karate', 'fitness for kids', 'discipline'],
        'secondary_interests': ['parenting', 'family activities', 'health and fitness'],
        'age_groups': ['25-54'],
        'budget_split': {'facebook': 40, 'instagram': 35, 'messenger': 25},
        'estimated_roas': 4.0,
        'objectives': ['OUTCOME_LEADS', 'OUTCOME_TRAFFIC'],
        'ad_types': ['image ads', 'video ads', 'carousel ads'],
    },
    'medical': {
        'primary_offering': 'services', 'peak_hours': 'business hours',
        'target_demographics': 'adults and families',
        'conversion_factors': ['credentials', 'location', 'insurance', 'reviews'],
        'primary_interests': ['health', 'wellness', 'medical care'],
        'secondary_interests': ['insurance', 'family health'],
        'age_groups': ['35-64'],
        'budget_split': {'facebook': 50, 'instagram': 25, 'messenger': 25},
        'estimated_roas': 4.0,
        'objectives': ['OUTCOME_LEADS', 'OUTCOME_TRAFFIC', 'OUTCOME_SALES'],
        'ad_types': ['image ads', 'text ads', 'video ads'],
    },
}


class ConfigStore:
    """Loads/saves per-industry rules from a JSON file that anyone can edit
    (by hand, or later through a dashboard form) without touching Python."""

    def __init__(self, path='industry_config.json'):
        self.path = path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                # Fill in any industries/fields present in defaults but missing
                # from the user's file, so upgrades never crash on a missing key.
                merged = json.loads(json.dumps(DEFAULT_INDUSTRY_CONFIG))
                for industry, cfg in loaded.items():
                    merged.setdefault(industry, {})
                    merged[industry].update(cfg)
                return merged
            except Exception as e:
                print(f"[ConfigStore] WARNING: could not read {self.path}, using defaults: {e}")
        return json.loads(json.dumps(DEFAULT_INDUSTRY_CONFIG))

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[ConfigStore] WARNING: could not save {self.path}: {e}")
            return False

    def get(self, industry):
        return self.data.get(industry, self.data.get('service', {}))

    def list_industries(self):
        return list(self.data.keys())

    def set(self, industry, config_dict):
        self.data[industry] = config_dict
        return self.save()

    def update(self, industry, partial_dict):
        current = self.data.get(industry, {})
        current.update(partial_dict)
        self.data[industry] = current
        return self.save()


# Single shared instance used everywhere in this file.
config_store = ConfigStore()

# ==================== META API INTEGRATION ====================

class MetaAPI:
    def __init__(self, access_token, ad_account_id, app_id=None, app_secret=None, page_token=None):
        self.access_token = access_token
        self.page_token = page_token or access_token
        self.ad_account_id = ad_account_id   # FIX: was 'ad_account'
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = "https://graph.facebook.com/v19.0"
        self.session = requests.Session()
        self.token_expires_at = None
        self._check_token_expiry()

    def _check_token_expiry(self):
        if not self.validate_token():
            self.token_expires_at = 0
            return
        try:
            r = self.session.get(f"{self.base_url}/debug_token", params={'input_token': self.access_token, 'access_token': self.access_token})
            data = r.json()
            if 'data' in data:
                expires_at = data['data'].get('expires_at', 0)
                data_access_expires = data['data'].get('data_access_expires_at', 0)
                if expires_at and expires_at > 0:
                    self.token_expires_at = expires_at
                elif data_access_expires and data_access_expires > 0:
                    self.token_expires_at = data_access_expires
                else:
                    self.token_expires_at = None
        except Exception:
            self.token_expires_at = 0

    def validate_token(self):
        try:
            r = self.session.get(f"{self.base_url}/me", params={'access_token': self.access_token, 'fields': 'id,name'})
            return r.status_code == 200 and 'id' in r.json()
        except Exception:
            return False

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def create_campaign(self, campaign_config):
        url = f"{self.base_url}/{self.ad_account_id}/campaigns"
        params = {'access_token': self.access_token}
        data = {
            "name": campaign_config.get('name', 'New Campaign'),
            "objective": campaign_config.get('objective', 'OUTCOME_LEADS'),
            "status": "PAUSED",
            "special_ad_categories": [],
        }
        if campaign_config.get('lifetime_budget'):
            data["lifetime_budget"] = int(campaign_config['lifetime_budget'] * 100)
        else:
            data["daily_budget"] = int(campaign_config.get('daily_budget', 50) * 100)

        try:
            response = self.session.post(url, params=params, json=data)
            result = response.json()
            print(f"Meta create_campaign response: {json.dumps(result, indent=2)[:300]}")
            return result
        except Exception as e:
            print(f"Meta create_campaign error: {e}")
            return {"error": str(e), "id": None}

    def create_ad_set(self, ad_set_config):
        url = f"{self.base_url}/{self.ad_account_id}/adsets"
        params = {'access_token': self.access_token}
        data = {
            "name": ad_set_config.get('name', 'New Ad Set'),
            "optimization_goal": ad_set_config.get('optimization_goal', 'OUTCOME_LEADS'),
            "billing_event": "IMPRESSIONS",
            "campaign_id": ad_set_config.get('campaign_id'),
            "status": "PAUSED",
            "targeting": ad_set_config.get('targeting', {}),
            "bid_strategy": "LOWEST_COST_WITH_BID_CAP",
            "bid_amount": 100,
        }
        if ad_set_config.get('page_id'):
            data['promoted_object'] = {'page_id': ad_set_config['page_id']}
        try:
            response = self.session.post(url, params=params, json=data)
            result = response.json()
            print(f"Meta create_ad_set response: {json.dumps(result, indent=2)[:300]}")
            return result
        except Exception as e:
            print(f"Meta create_ad_set error: {e}")
            return {"error": str(e), "id": None}

    def create_ad(self, ad_config):
        url = f"{self.base_url}/{self.ad_account_id}/ads"
        params = {'access_token': self.access_token}
        data = {
            "name": ad_config.get('name', 'New Ad'),
            "adset_id": ad_config.get('adset_id'),
            "status": ad_config.get('status', 'PAUSED'),
            "creative": ad_config.get('creative', {})
        }
        try:
            response = self.session.post(url, params=params, json=data)
            result = response.json()
            print(f"Meta create_ad response: {json.dumps(result, indent=2)[:300]}")
            return result
        except Exception as e:
            print(f"Meta create_ad error: {e}")
            return {"error": str(e), "id": None}

    def get_page_id(self):
        url = f"{self.base_url}/me/accounts"
        params = {'access_token': self.access_token, 'limit': 5, 'fields': 'id,name'}
        try:
            resp = self.session.get(url, params=params)
            pages = resp.json().get('data', [])
            if pages:
                return pages[0]['id']
        except Exception:
            pass
        return None

    def get_campaigns(self, limit=50):
        url = f"{self.base_url}/{self.ad_account_id}/campaigns"
        params = {'access_token': self.access_token, 'limit': limit,
                  'fields': 'id,name,status,objective,daily_budget,lifetime_budget',
                  'effective_status': '["ACTIVE","PAUSED","ARCHIVED"]'}
        try:
            response = self.session.get(url, params=params)
            return response.json().get('data', [])
        except Exception as e:
            return []

    def get_ad_sets(self, campaign_id=None, limit=50):
        url = f"{self.base_url}/{self.ad_account_id}/adsets"
        params = {'access_token': self.access_token, 'limit': limit,
                  'fields': 'id,name,status,daily_budget,optimization_goal'}
        if campaign_id:
            params['campaign_id'] = campaign_id
        try:
            response = self.session.get(url, params=params)
            return response.json().get('data', [])
        except Exception as e:
            return []

    def get_ads(self, adset_id=None, limit=50):
        url = f"{self.base_url}/{self.ad_account_id}/ads"
        params = {'access_token': self.access_token, 'limit': limit,
                  'fields': 'id,name,status,creative'}
        if adset_id:
            params['adset_id'] = adset_id
        try:
            response = self.session.get(url, params=params)
            return response.json().get('data', [])
        except Exception as e:
            return []

    def get_insights(self, campaign_id=None, adset_id=None, insights_fields=None):
        url = f"{self.base_url}/{self.ad_account_id}/insights"
        params = {
            'access_token': self.access_token,
            'fields': insights_fields or 'spend,clicks,actions,ctr,cpc,cpm,impressions',
            'time_increment': 'day',
            'date_preset': 'last_30d'
        }
        if campaign_id:
            params['filtering'] = json.dumps([{"field": "campaign.id", "operator": "IN", "value": [campaign_id]}])
        try:
            response = self.session.get(url, params=params)
            return response.json().get('data', [])
        except Exception as e:
            return []

    def refresh_access_token(self):
        if self.app_id and self.app_secret and self.access_token:
            url = f"{self.base_url}/oauth/access_token"
            params = {
                'grant_type': 'fb_exchange_token',
                'client_id': self.app_id,
                'client_secret': self.app_secret,
                'fb_exchange_token': self.access_token,
            }
            try:
                response = self.session.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    new_token = data.get('access_token')
                    if new_token:
                        self.access_token = new_token
                        return True
            except Exception:
                pass
            params2 = {
                'client_id': self.app_id,
                'client_secret': self.app_secret,
                'grant_type': 'client_credentials'
            }
            try:
                response = self.session.get(url, params=params2)
                if response.status_code == 200:
                    new_token = response.json().get('access_token')
                    if new_token:
                        self.access_token = new_token
                        return True
            except Exception:
                pass
        return False

    def refresh_page_token(self, page_id):
        url = f"{self.base_url}/{page_id}"
        params = {'access_token': self.access_token, 'fields': 'access_token'}
        try:
            response = self.session.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if 'access_token' in data:
                    self.page_token = data['access_token']
                    return True
        except Exception:
            pass
        return False

    def upload_image(self, image_path):
        url = f"{self.base_url}/{self.ad_account_id}/adimages"
        safe = os.path.normpath(image_path.lstrip('/')).replace('\\', '/')
        if '..' in safe or safe.startswith('/'):
            print(f"Image path rejected (possible traversal): {image_path}")
            return None
        full_path = os.path.join(os.path.dirname(__file__), safe)
        full_path = os.path.normpath(full_path)
        if not full_path.startswith(os.path.normpath(os.path.dirname(__file__))):
            print(f"Image path rejected (outside app dir): {full_path}")
            return None
        if not os.path.exists(full_path):
            print(f"Image file not found: {full_path}")
            return None
        try:
            with open(full_path, 'rb') as f:
                files = {'filename': f}
                params = {'access_token': self.access_token}
                response = self.session.post(url, params=params, files=files)
                result = response.json()
                print(f"Meta upload_image response: {json.dumps(result, indent=2)[:500]}")
                if 'images' in result:
                    for fname, info in result['images'].items():
                        return info.get('hash')
                return None
        except Exception as e:
            print(f"Meta upload_image error: {e}")
            return None

    def upload_video(self, video_path):
        url = f"{self.base_url}/{self.ad_account_id}/advideos"
        safe = os.path.normpath(video_path.lstrip('/')).replace('\\', '/')
        if '..' in safe or safe.startswith('/'):
            print(f"Video path rejected (possible traversal): {video_path}")
            return None
        full_path = os.path.join(os.path.dirname(__file__), safe)
        full_path = os.path.normpath(full_path)
        if not full_path.startswith(os.path.normpath(os.path.dirname(__file__))):
            print(f"Video path rejected (outside app dir): {full_path}")
            return None
        if not os.path.exists(full_path):
            print(f"Video file not found: {full_path}")
            return None
        try:
            params = {'access_token': self.access_token}
            with open(full_path, 'rb') as f:
                files = {'source': f}
                response = self.session.post(url, params=params, files=files)
                result = response.json()
            print(f"Meta upload_video response: {json.dumps(result, indent=2)[:500]}")
            if 'id' in result:
                return result['id']
            return None
        except Exception as e:
            print(f"Meta upload_video error: {e}")
            return None

    def archive_campaign(self, campaign_id):
        url = f"{self.base_url}/{campaign_id}"
        params = {'access_token': self.access_token, 'status': 'ARCHIVED'}
        try:
            response = self.session.post(url, params=params)
            return response.json()
        except Exception as e:
            return {'error': str(e)}

    def get_instagram_business_account_id(self, page_id):
        for token in [self.page_token, self.access_token]:
            if not token:
                continue
            url = f"{self.base_url}/{page_id}"
            params = {'access_token': token, 'fields': 'instagram_business_account{id,username}'}
            try:
                response = self.session.get(url, params=params)
                data = response.json()
                ig = data.get('instagram_business_account')
                if ig:
                    return ig.get('id')
            except:
                pass
        return None

    def create_facebook_post(self, page_id, message, media_url=None, scheduled_time=None, page_token=None):
        token = page_token or self.page_token or self.access_token
        url = f"{self.base_url}/{page_id}/feed"
        params = {'access_token': token}
        if media_url:
            params['message'] = message
            params['attached_media'] = json.dumps([{'media_fbid': media_url}])
        else:
            params['message'] = message
        if scheduled_time:
            params['published'] = 'false'
            params['scheduled_publish_time'] = int(scheduled_time)
        try:
            response = self.session.post(url, params=params)
            return response.json()
        except Exception as e:
            return {'error': str(e)}

    def create_facebook_photo_post(self, page_id, image_path, caption='', page_token=None):
        token = page_token or self.page_token or self.access_token
        url = f"{self.base_url}/{page_id}/photos"
        full_path = os.path.join(os.path.dirname(__file__), image_path.lstrip('/'))
        if not os.path.exists(full_path):
            return {'error': f'Image not found: {full_path}'}
        try:
            with open(full_path, 'rb') as f:
                files = {'source': f}
                params = {'access_token': token, 'caption': caption}
                response = self.session.post(url, params=params, files=files)
                return response.json()
        except Exception as e:
            return {'error': str(e)}

    def create_facebook_carousel_post(self, page_id, image_urls, message='', scheduled_time=None, page_token=None):
        token = page_token or self.page_token or self.access_token
        media_ids = []
        for url in image_urls:
            try:
                if url.startswith('http'):
                    r = self.session.post(f"{self.base_url}/{page_id}/photos", params={
                        'access_token': token, 'url': url, 'published': 'false'
                    })
                else:
                    full_path = os.path.join(os.path.dirname(__file__), url.lstrip('/'))
                    if not os.path.exists(full_path):
                        continue
                    with open(full_path, 'rb') as f:
                        r = self.session.post(f"{self.base_url}/{page_id}/photos", params={
                            'access_token': token, 'published': 'false'
                        }, files={'source': f})
                data = r.json()
                if data.get('id'):
                    media_ids.append(data['id'])
            except:
                pass
        if not media_ids:
            return {'error': 'No images could be uploaded for carousel'}
        params = {'access_token': token, 'message': message, 'attached_media': json.dumps([{'media_fbid': mid} for mid in media_ids])}
        if scheduled_time:
            params['published'] = 'false'
            params['scheduled_publish_time'] = int(scheduled_time)
        try:
            r = self.session.post(f"{self.base_url}/{page_id}/feed", params=params)
            return r.json()
        except Exception as e:
            return {'error': str(e)}

    def create_facebook_video_post(self, page_id, video_path, description='', scheduled_time=None, page_token=None):
        token = page_token or self.page_token or self.access_token
        full_path = os.path.join(os.path.dirname(__file__), video_path.lstrip('/'))
        if not os.path.exists(full_path):
            return {'error': f'Video not found: {full_path}'}
        try:
            with open(full_path, 'rb') as f:
                files = {'source': f}
                params = {'access_token': token, 'description': description}
                if scheduled_time:
                    params['published'] = 'false'
                    params['scheduled_publish_time'] = int(scheduled_time)
                r = self.session.post(f"{self.base_url}/{page_id}/videos", params=params, files=files)
                return r.json()
        except Exception as e:
            return {'error': str(e)}

    def update_campaign(self, campaign_id, **kwargs):
        url = f"{self.base_url}/{campaign_id}"
        params = {'access_token': self.access_token}
        for k, v in kwargs.items():
            params[k] = v
        try:
            response = self.session.post(url, params=params)
            return response.json()
        except Exception as e:
            return {'error': str(e)}

    def get_campaign_insights(self, campaign_id, fields='spend,clicks,impressions,ctr,cpc,actions'):
        url = f"{self.base_url}/{campaign_id}/insights"
        params = {
            'access_token': self.access_token,
            'fields': fields,
            'date_preset': 'last_30d',
            'limit': 1
        }
        try:
            response = self.session.get(url, params=params)
            data = response.json().get('data', [])
            return data[0] if data else {}
        except Exception as e:
            return {}

    def _ig_token(self):
        return self.page_token or self.access_token

    def _upload_to_facebook_for_ig(self, image_path, page_id, token):
        full_path = os.path.join(os.path.dirname(__file__), image_path.lstrip('/'))
        if not os.path.exists(full_path):
            return None
        try:
            with open(full_path, 'rb') as f:
                r = self.session.post(f"{self.base_url}/{page_id}/photos", params={
                    'access_token': token, 'published': 'false'
                }, files={'source': f})
                data = r.json()
                if data.get('id'):
                    return f"{self.base_url}/{data['id']}/picture?access_token={token}"
        except:
            pass
        return None

    def delete_facebook_post(self, post_id, page_token=None):
        """Delete a post from Facebook by its post_id (format: page_id_post_id)"""
        token = page_token or self.page_token or self.access_token
        try:
            url = f"{self.base_url}/{post_id}"
            r = self.session.delete(url, params={'access_token': token})
            data = r.json()
            if data.get('success') or data.get('result') == 'true':
                return {'success': True}
            # Facebook sometimes returns True as boolean
            if data is True or data == True:
                return {'success': True}
            return {'success': False, 'error': data.get('error', {}).get('message', 'Unknown error')}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def delete_instagram_post(self, ig_media_id, page_token=None):
        """Delete an Instagram post by its media ID"""
        token = page_token or self.page_token or self.access_token
        try:
            url = f"{self.base_url}/{ig_media_id}"
            r = self.session.delete(url, params={'access_token': token})
            data = r.json()
            if data.get('success') or data is True:
                return {'success': True}
            return {'success': False, 'error': data.get('error', {}).get('message', 'Unknown error')}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _ensure_public_url(self, media_url, page_id, token):
        if media_url.startswith('/uploads/') or media_url.startswith('uploads/'):
            fb_url = self._upload_to_facebook_for_ig(media_url, page_id, token)
            if fb_url:
                return fb_url
        return media_url

    def create_instagram_post(self, ig_id, image_url, caption='', scheduled_time=None, page_id=None):
        token = self._ig_token()
        if page_id:
            image_url = self._ensure_public_url(image_url, page_id, token)
        container_url = f"{self.base_url}/{ig_id}/media"
        params = {
            'access_token': token,
            'image_url': image_url,
            'caption': caption
        }
        if scheduled_time:
            params['published'] = 'false'
            params['scheduled_publish_time'] = int(scheduled_time)
        try:
            container_resp = self.session.post(container_url, params=params)
            container_data = container_resp.json()
            container_id = container_data.get('id')
            if not container_id:
                return container_data
            if scheduled_time:
                return {'id': container_id, 'scheduled': True}
            publish_url = f"{self.base_url}/{ig_id}/media_publish"
            pub_params = {'access_token': token, 'creation_id': container_id}
            pub_resp = self.session.post(publish_url, params=pub_params)
            return pub_resp.json()
        except Exception as e:
            return {'error': str(e)}

    def create_instagram_carousel_post(self, ig_id, image_urls, caption='', scheduled_time=None, page_id=None):
        token = self._ig_token()
        if page_id:
            image_urls = [self._ensure_public_url(u, page_id, token) for u in image_urls]
        children = []
        for url in image_urls:
            item_url = f"{self.base_url}/{ig_id}/media"
            item_params = {'access_token': token, 'image_url': url, 'is_carousel_item': 'true'}
            try:
                r = self.session.post(item_url, params=item_params)
                d = r.json()
                if d.get('id'):
                    children.append(d['id'])
            except:
                pass
        if not children:
            return {'error': 'No carousel items created'}
        container_url = f"{self.base_url}/{ig_id}/media"
        params = {
            'access_token': token,
            'media_type': 'CAROUSEL',
            'children': json.dumps(children),
            'caption': caption
        }
        if scheduled_time:
            params['published'] = 'false'
            params['scheduled_publish_time'] = int(scheduled_time)
        try:
            container_resp = self.session.post(container_url, params=params)
            container_data = container_resp.json()
            container_id = container_data.get('id')
            if not container_id:
                return container_data
            if scheduled_time:
                return {'id': container_id, 'scheduled': True}
            publish_url = f"{self.base_url}/{ig_id}/media_publish"
            pub_params = {'access_token': token, 'creation_id': container_id}
            pub_resp = self.session.post(publish_url, params=pub_params)
            return pub_resp.json()
        except Exception as e:
            return {'error': str(e)}

    def get_lead_forms(self, page_id):
        url = f"{self.base_url}/{page_id}/leadgen_forms"
        params = {'access_token': self.access_token, 'fields': 'id,name,status,created_time'}
        try:
            response = self.session.get(url, params=params)
            data = response.json()
            return data.get('data', [])
        except Exception as e:
            print(f"Error getting lead forms: {e}")
            return []

    def get_leads(self, form_id):
        url = f"{self.base_url}/{form_id}/leads"
        params = {'access_token': self.access_token, 'fields': 'id,created_time,field_data'}
        try:
            response = self.session.get(url, params=params)
            data = response.json()
            leads = data.get('data', [])
            results = []
            for lead in leads:
                fields = {}
                for fd in lead.get('field_data', []):
                    fields[fd.get('name')] = fd.get('values', [''])[0]
                results.append({
                    'id': lead['id'],
                    'created_time': lead.get('created_time'),
                    'fields': fields
                })
            return results
        except Exception as e:
            print(f"Error getting leads: {e}")
            return []


# ==================== AUDIENCE ANALYZER ====================

class AudienceAnalyzer:
    def __init__(self):
        pass  # targeting rules now live in config_store (industry_config.json), not here

    def identify_target_audience(self, business_profile):
        industry = business_profile.get('industry', 'service')
        location = business_profile.get('location', '')
        cfg = config_store.get(industry)
        return {
            'primary_audience': cfg.get('primary_interests', []),
            'secondary_audience': cfg.get('secondary_interests', []),
            'geographic_targeting': self.get_geographic_targeting(location),
            'interest_targeting': cfg.get('primary_interests', []),
            'behavioral_targeting': ['mobile shopper', 'price sensitive', 'brand loyal']
        }

    def get_geographic_targeting(self, location):
        return {
            'primary_location': location,
            'radius': 25,
            'countries': ['US'],
        }

    def get_interest_targeting(self, industry):
        return config_store.get(industry).get('primary_interests', [])

    def get_behavioral_targeting(self, industry):
        return ['mobile shopper', 'price sensitive', 'brand loyal', 'social media user']


# ==================== AI STRATEGY ENGINE ====================

class AIStrategyEngine:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.audience_analyzer = AudienceAnalyzer()  # FIX: instantiate here

    def analyze_business(self, business_profile):
        industry = business_profile.get('industry', 'service')
        return {
            'industry_insights': self.get_industry_insights(industry),
            'target_audience': self.audience_analyzer.identify_target_audience(business_profile),
            'budget_recommendations': self.recommend_budget(business_profile),
            'competition_level': self.assess_competition(business_profile),
            'seasonal_trends': self.analyze_seasonal_trends(industry, business_profile)
        }

    def generate_strategy(self, business_analysis):
        industry_insights = business_analysis.get('industry_insights', {})
        budget = business_analysis.get('budget_recommendations', {})
        return {
            'campaign_name': f"{industry_insights.get('primary_offering', 'Business')} Campaign - {datetime.now().strftime('%Y-%m-%d')}",
            'objectives': self.select_objectives(industry_insights),
            'ad_types': self.select_ad_types(industry_insights),
            'targeting_strategies': list(business_analysis.get('target_audience', {}).get('interest_targeting', [])),
            'budget_allocation': budget,
            'estimated_roas': self.estimate_roas(industry_insights),
            'recommended_bidding': self.recommend_bidding(industry_insights)
        }

    def get_industry_insights(self, industry):
        cfg = config_store.get(industry)
        return {
            'industry': industry if industry in config_store.data else 'service',
            'primary_offering': cfg.get('primary_offering', 'Professional Services'),
            'peak_hours': cfg.get('peak_hours', 'business hours'),
            'target_demographics': cfg.get('target_demographics', 'adults'),
            'conversion_factors': cfg.get('conversion_factors', []),
        }

    def recommend_budget(self, business_profile):
        industry = business_profile.get('industry', 'service')
        return config_store.get(industry).get('budget_split', config_store.get('service').get('budget_split', {}))

    def assess_competition(self, business_profile):
        size = business_profile.get('business_size', 'small')
        return {'large': 'high', 'medium': 'medium'}.get(size, 'low')

    def analyze_seasonal_trends(self, industry, business_profile):
        patterns = {
            'restaurant': {
                'peak_months': ['March', 'April', 'May', 'September', 'October', 'November'],
                'promotional_events': ['Happy Hour', 'Lunch Special', 'Weekend Brunch']
            },
            'retail': {
                'peak_months': ['November', 'December', 'January', 'March', 'April'],
                'promotional_events': ['Black Friday', 'Cyber Monday', 'Clearance Sales']
            }
        }
        return patterns.get(industry, patterns['retail'])

    def select_objectives(self, industry_insights):
        industry = industry_insights.get('industry', 'service')
        return config_store.get(industry).get('objectives', config_store.get('service').get('objectives', ['OUTCOME_LEADS']))

    def select_ad_types(self, industry_insights):
        industry = industry_insights.get('industry', 'service')
        return config_store.get(industry).get('ad_types', config_store.get('service').get('ad_types', ['image ads']))

    def estimate_roas(self, industry_insights):
        industry = industry_insights.get('industry', 'service')
        return config_store.get(industry).get('estimated_roas', 4.0)

    def recommend_bidding(self, industry_insights):
        return {
            'bidding_strategy': 'LOWEST_COST',
            'optimization_goal': 'LINK_CLICKS' if industry_insights.get('industry') == 'service' else 'OFFSITE_CONVERSIONS',
            'bid_cap': 'auto',
            'budget_pacing': 'even'
        }

    def _generate_with_ai(self, prompt, api_key, provider='groq'):
        if not api_key:
            return None
        try:
            if provider == 'groq':
                url = 'https://api.groq.com/openai/v1/chat/completions'
                headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
                body = {'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.9, 'max_tokens': 1024}
            elif provider == 'gemini':
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
                headers = {'Content-Type': 'application/json'}
                body = {'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': {'temperature': 0.9, 'maxOutputTokens': 1024}}
            else:
                return None
            resp = requests.post(url, json=body, headers=headers, timeout=20)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if provider == 'groq':
                text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            else:
                text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            return text
        except:
            return None

    def generate_ad_copy(self, business_profile, language='en'):
        ai_key = business_profile.get('ai_api_key') or getattr(self, 'ai_api_key', None)
        ai_provider = business_profile.get('ai_provider') or getattr(self, 'ai_provider', 'groq')
        name = business_profile.get('business_name', 'Your Business' if language == 'en' else 'Tu Negocio')
        location = business_profile.get('location', 'your area' if language == 'en' else 'tu zona')
        description = business_profile.get('description', '')
        headline = business_profile.get('headline', '')
        message = business_profile.get('message', '')

        if ai_key:
            lang_instruction = 'Write everything in Spanish.' if language == 'es' else 'Write everything in English.'
            prompt = f"""You are a Facebook ads copywriter.

Business name: {name}
Location: {location}
Description: {description}
Target headline: {headline}
Target message: {message}

{lang_instruction}

Generate exactly 5 different headlines (max 40 characters each) and 3 different primary text messages (max 500 characters each) for Facebook ads. Make each one unique, persuasive, and locally relevant. Return them in this format:

HEADLINES:
1. headline one
2. headline two
3. headline three
4. headline four
5. headline five

MESSAGES:
1. message one
2. message two
3. message three"""

            result = self._generate_with_ai(prompt, ai_key, ai_provider)
            if result:
                headlines = []
                messages = []
                current = None
                for line in result.split('\n'):
                    line = line.strip()
                    if line.upper().startswith('HEADLINES'):
                        current = 'headlines'
                    elif line.upper().startswith('MESSAGES'):
                        current = 'messages'
                    elif line and line[0].isdigit() and '. ' in line[:4]:
                        text = line.split('. ', 1)[1].strip()
                        if current == 'headlines' and len(headlines) < 5:
                            headlines.append(text[:40])
                        elif current == 'messages' and len(messages) < 3:
                            messages.append(text[:500])
                if len(headlines) >= 3 and len(messages) >= 1:
                    while len(headlines) < 5:
                        headlines.append(headlines[-1])
                    while len(messages) < 3:
                        messages.append(messages[-1])
                    return {'headlines': headlines[:5], 'messages': messages[:3]}

        industry = business_profile.get('industry', 'service')
        insights = self.get_industry_insights(industry)
        demos = insights.get('target_demographics', 'everyone')
        offerings = insights.get('primary_offering', 'services' if language == 'en' else 'servicios')

        if language == 'es':
            demos_es = {'adults': 'adultos', 'families': 'familias', 'professionals': 'profesionales', 'seniors': 'personas mayores', 'everyone': 'todos'}
            demos = demos_es.get(demos, demos)
            offerings_es = {'services': 'servicios', 'products': 'productos', 'consultation': 'consultas', 'training': 'entrenamiento', 'classes': 'clases'}
            offerings = offerings_es.get(offerings, offerings)
            headlines = [
                f"Oferta Especial - {name}",
                f"Descubre los Mejores {offerings} en {location}",
                f"Oferta Exclusiva para {demos.title()}",
                f"Tiempo Limitado - Promoción en {name}",
                f"Experimenta {offerings} Premium Hoy"
            ]
            messages = [
                f"¿Buscas {offerings} de calidad en {location}? {name} ofrece lo mejor para {demos}. ¡Visítanos hoy!",
                f"¡No te pierdas esto! {name} tiene una promoción exclusiva para {demos}. Contáctanos ahora.",
                f"¿Listo para la mejor experiencia en {offerings}? {name} en {location} está aquí para ti. ¡Llama o visítanos!"
            ]
        else:
            headlines = [
                f"Special Offer - {name}",
                f"Discover Top {offerings} in {location}",
                f"Exclusive Deal Just for {demos.title()}",
                f"Limited Time - {name} Promotion",
                f"Experience Premium {offerings} Today"
            ]
            messages = [
                f"Looking for quality {offerings.lower()} in {location}? {name} offers the best for {demos}. Visit us today!",
                f"Don't miss out! {name} is offering an exclusive promotion for {demos}. Contact us now to learn more.",
                f"Ready for the best {offerings.lower()} experience? {name} in {location} is here for you. Call or visit today!"
            ]

        return {
            'headlines': headlines,
            'messages': messages
        }


# ==================== PERFORMANCE OPTIMIZER ====================

class PerformanceOptimizer:
    def __init__(self, meta_api=None, safety_guard_instance=None):
        self.meta_api = meta_api
        self.monitoring_active = False
        self.safety_guard = safety_guard_instance or safety_guard

    def start_monitoring(self, campaign_id):
        self.monitoring_active = True
        t = threading.Timer(3600, self.check_performance, args=[campaign_id])
        t.daemon = True
        t.start()

    def check_performance(self, campaign_id):
        if not self.monitoring_active:
            return
        metrics = self.get_campaign_metrics(campaign_id)
        analysis = self.analyze_performance(metrics)
        optimizations = self.generate_optimizations(analysis)
        self.apply_optimizations(optimizations, campaign_id)
        t = threading.Timer(3600, self.check_performance, args=[campaign_id])
        t.daemon = True
        t.start()

    def get_campaign_metrics(self, campaign_id):
        if self.meta_api:
            insights = self.meta_api.get_insights(campaign_id=campaign_id)
            if insights and len(insights) > 0:
                i = insights[0]
                actions = i.get('actions', [])
                lead_forms = 0
                for a in actions:
                    if a.get('action_type') in ['lead', 'form_submit', 'leadgen']:
                        lead_forms = int(a.get('value', 0))
                        break
                spend = float(i.get('spend', 0))
                clicks = int(i.get('clicks', 0))
                impressions = int(i.get('impressions', 0))
                ctr = float(i.get('ctr', 0))
                cpc = float(i.get('cpc', 0))
                cpm = float(i.get('cpm', 0))
                return {
                    'spend': spend,
                    'clicks': clicks,
                    'impressions': impressions,
                    'lead_forms_submit': lead_forms,
                    'ctr': ctr,
                    'cpc': cpc,
                    'cpm': cpm,
                    'conversion_rate': round(lead_forms / max(clicks, 1), 2) if clicks else 0,
                    'roas': round(lead_forms * 50 / max(spend, 1), 2) if spend else 0,
                    'frequency': round(impressions / max(clicks, 1), 2) if clicks else 1.5
                }
        return {
            'spend': 0, 'clicks': 0, 'impressions': 0, 'lead_forms_submit': 0,
            'ctr': 0, 'cpc': 0, 'cpm': 0, 'conversion_rate': 0, 'roas': 0, 'frequency': 1.5
        }

    def analyze_performance(self, metrics):
        return {
            'performance_grade': self.calculate_performance_grade(metrics),
            'lead_generation': self.analyze_lead_generation(metrics),
            'budget_efficiency': self.analyze_budget_efficiency(metrics),
            'targeting_effectiveness': self.analyze_targeting_effectiveness(metrics),
            'optimization_opportunities': self.find_optimization_opportunities(metrics)
        }

    def calculate_performance_grade(self, metrics):
        roas = metrics.get('roas', 0)
        cpa = metrics.get('spend', 1) / max(metrics.get('lead_forms_submit', 1), 1)
        if roas > 5 and cpa < 10:
            return 'Excellent'
        elif roas > 3 and cpa < 20:
            return 'Good'
        elif roas > 2 and cpa < 50:
            return 'Average'
        return 'Poor'

    def analyze_lead_generation(self, metrics):
        leads = max(metrics.get('lead_forms_submit', 1), 1)
        return {
            'lead_volume': metrics.get('lead_forms_submit', 0),
            'lead_cost': round(metrics.get('spend', 0) / leads, 2),
            'lead_quality': 'Good' if metrics.get('conversion_rate', 0) > 0.2 else 'Average'
        }

    def generate_pdf_reports(self, campaign_data, business_profile, weeks=4):
        """Generate comprehensive PDF performance reports"""
        try:
            from io import BytesIO
            from fpdf import FPDF
            from datetime import datetime
            
            pdf = FPDF()
            pdf.add_page()
            
            # Title Page
            pdf.set_font("Arial", "B", 20)
            pdf.cell(0, 10, "Campaign Performance Report", 0, 1, "C")
            pdf.set_font("Arial", "", 12)
            pdf.cell(0, 10, f"Business: {business_profile.get('business_name', 'Business')}", 0, 1, "C")
            pdf.cell(0, 10, f"Period: Last {weeks} weeks", 0, 1, "C")
            pdf.cell(0, 10, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, "C")
            
            pdf.ln(20)
            
            # Executive Summary
            pdf.set_font("Arial", "B", 16)
            pdf.cell(0, 10, "Executive Summary", 0, 1)
            pdf.set_font("Arial", "", 11)
            
            # Calculate metrics
            total_spend = sum(c.get('spend', 0) for c in campaign_data)
            total_revenue = sum(c.get('revenue', 0) for c in campaign_data)
            total_conversions = sum(c.get('conversions', 0) for c in campaign_data)
            
            roi = (total_revenue - total_spend) / total_spend if total_spend > 0 else 0
            
            metrics = [
                f"Total Investment: ${total_spend:,.2f}",
                f"Total Revenue Generated: ${total_revenue:,.2f}",
                f"ROI: {roi:.1%}",
                f"Total Conversions: {total_conversions:,}",
                f"Average CPA: ${total_spend / max(total_conversions, 1):,.2f}",
                f"Conversion Rate: {(total_conversions / max(total_spend/10, 1)):.2%}"
            ]
            
            for metric in metrics:
                pdf.cell(0, 8, metric, 0, 1)
            
            pdf.ln(10)
            
            # Campaign Performance
            pdf.set_font("Arial", "B", 16)
            pdf.cell(0, 10, "Campaign Performance", 0, 1)
            
            campaigns = campaign_data[:5]
            for i, campaign in enumerate(campaigns):
                pdf.set_font("Arial", "B", 12)
                pdf.cell(0, 8, f"{campaign.get('name', 'Campaign ' + str(i+1))}", 0, 1)
                pdf.set_font("Arial", "", 11)
                
                campaign_metrics = [
                    f"Spend: ${campaign.get('spend', 0):,.2f}",
                    f"Revenue: ${campaign.get('revenue', 0):,.2f}",
                    f"ROI: {(campaign.get('revenue', 0) - campaign.get('spend', 0)) / max(campaign.get('spend', 1), 1):.1%}",
                    f"Conversions: {campaign.get('conversions', 0)}",
                    f"CTR: {campaign.get('ctr', 0):.2%}",
                    f"CPA: ${campaign.get('cpa', 0):,.2f}"
                ]
                
                for metric in campaign_metrics:
                    pdf.cell(0, 6, metric, 0, 1)
                pdf.ln(5)
            
            # Generate Report
            pdf_output = BytesIO()
            pdf_bytes = pdf.output(dest='S')
            pdf_output.write(pdf_bytes)
            pdf_output.seek(0)
            
            return pdf_output, "PDF report generated successfully"
            
        except Exception as e:
            print(f"Error generating PDF reports: {e}")
            return None, "PDF generation failed"
    
    def generate_ad_copy_with_ai(self, business_profile, objective='lead_generation', count=3):
        """Generate multiple ad copy variations using OpenAI"""
        if not self.openai_client:
            return []
        
        industry = business_profile.get('industry', 'service')
        business_name = business_profile.get('business_name', 'Your Business')
        
        prompt = f"""
        Create {count} different Facebook ad copy variations for {business_name}.
        
        Business Profile:
        - Industry: {industry}
        - Target audience: {business_profile.get('target_audience', 'local customers')}
        - Objective: {objective}
        - Key selling points: {business_profile.get('key_points', 'professional services')}
        
        Each ad should include:
        - Hook (strong opening)
        - Problem statement
        - Solution (your business)
        - Call to action with emoji
        - Length: 40-80 characters for hook, 80-150 for body
        
        Make them different in tone and approach.
        Return only the copy, one per line.
        """
        
        try:
            response = self.openai_client.ChatCompletion.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.7
            )
            
            copies = []
            for choice in response['choices']:
                copy = choice['message']['content'].strip()
                if copy and len(copy) > 20:
                    copies.append(copy)
            
            return copies[:count]
        except Exception as e:
            print(f"Error generating ad copy: {e}")
            return []
    
    def generate_image_with_ai(self, prompt, size="1024x1024"):
        """Generate image using DALL-E if available"""
        if not self.openai_client:
            return None
        
        try:
            response = self.openai_client.Image.create(
                model="dall-e-3",
                prompt=prompt,
                n=1,
                size=size,
                quality="standard"
            )
            
            image_url = response['data'][0]['url']
            import requests as rq
            img_response = rq.get(image_url)
            if img_response.status_code == 200:
                return img_response.content
            return None
        except Exception as e:
            print(f"Error generating image: {e}")
            return None

    def analyze_targeting_effectiveness(self, metrics):
        return {
            'ctr': metrics.get('ctr', 0),
            'frequency': metrics.get('frequency', 0)
        }

    def find_optimization_opportunities(self, metrics):
        opportunities = []
        if metrics.get('ctr', 0) < 0.02:
            opportunities.append('Improve ad creative (low CTR)')
        if metrics.get('spend', 0) / max(metrics.get('lead_forms_submit', 1), 1) > 50:
            opportunities.append('Adjust bidding strategy (high CPA)')
        if metrics.get('frequency', 0) > 3:
            opportunities.append('Reduce frequency cap (ad fatigue)')
        return opportunities

    def generate_optimizations(self, analysis):
        grade = analysis.get('performance_grade', 'Average')
        self._last_grade = grade
        if grade == 'Poor':
            return ['adjust_budget_allocation', 'test_new_audiences', 'refresh_ad_creative']
        elif grade == 'Average':
            return ['optimize_targeting', 'improve_ad_copy', 'scale_successful_elements']
        return ['increase_budget', 'expand_successful_targeting', 'duplicate_successful_creative']

    def apply_optimizations(self, optimizations, campaign_id=None):
        if not self.meta_api or not campaign_id:
            for opt in optimizations:
                print(f"Action ({opt}): No-op (no meta_api or campaign_id)")
            return []
        results = []
        campaign = None
        try:
            camps = self.meta_api.get_campaigns(limit=100)
            for c in camps:
                if c.get('id') == campaign_id:
                    campaign = c
                    break
        except:
            pass
        current_budget = None
        was_archived = False
        if campaign:
            current_budget = campaign.get('daily_budget') or campaign.get('lifetime_budget')
            if current_budget:
                current_budget = float(current_budget) / 100
            if campaign.get('status') == 'ARCHIVED':
                was_archived = True
                try:
                    self.meta_api.update_campaign(campaign_id, status='PAUSED')
                    results.append("Campaign unarchived (set to PAUSED)")
                except Exception as e:
                    results.append(f"Could not unarchive: {e}")

        for opt in optimizations:
            try:
                if opt == 'increase_budget' and current_budget:
                    if not self.safety_guard.auto_budget_increase_enabled:
                        results.append(f"Suggestion (not applied — auto budget increases are OFF): raise to ~${min(current_budget * 1.2, 500):.0f}/day")
                        self.safety_guard.log('increase_budget', allowed=False, reason='AUTO_BUDGET_INCREASE_ENABLED is off', campaign_id=campaign_id)
                    else:
                        new_budget = min(current_budget * 1.2, 500)
                        ok, reason = self.safety_guard.check_new_budget(getattr(self, 'campaigns_ref', {}) or {}, new_budget - current_budget, action='increase_budget', campaign_id=campaign_id)
                        if not ok:
                            results.append(f"Budget increase BLOCKED by safety cap: {reason}")
                        else:
                            result = self.meta_api.update_campaign(campaign_id, daily_budget=int(new_budget * 100))
                            results.append(f"Budget increased: ${current_budget:.0f} → ${new_budget:.0f}/day")
                            print(f"Action: increase_budget -> ${new_budget:.0f}/day")

                elif opt == 'adjust_budget_allocation' and current_budget:
                    grade = getattr(self, '_last_grade', 'Average')
                    factor = 1.3 if grade in ('Excellent', 'Good') else (0.8 if grade == 'Poor' else 1.0)
                    new_budget = min(current_budget * factor, 500)
                    if factor > 1 and not self.safety_guard.auto_budget_increase_enabled:
                        results.append(f"Suggestion (not applied — auto budget increases are OFF): {grade} performance, could raise to ~${new_budget:.0f}/day")
                        self.safety_guard.log('adjust_budget_allocation', allowed=False, reason='AUTO_BUDGET_INCREASE_ENABLED is off', campaign_id=campaign_id)
                    else:
                        ok, reason = (True, 'decrease, no cap check needed') if factor <= 1 else self.safety_guard.check_new_budget(getattr(self, 'campaigns_ref', {}) or {}, new_budget - current_budget, action='adjust_budget_allocation', campaign_id=campaign_id)
                        if not ok:
                            results.append(f"Budget adjustment BLOCKED by safety cap: {reason}")
                        else:
                            result = self.meta_api.update_campaign(campaign_id, daily_budget=int(new_budget * 100))
                            direction = 'increased' if factor > 1 else 'decreased'
                            results.append(f"Budget {direction}: ${current_budget:.0f} → ${new_budget:.0f}/day ({grade})")
                            print(f"Action: adjust_budget_allocation -> {direction} to ${new_budget:.0f}/day")

                elif opt == 'test_new_audiences':
                    results.append("Suggestion: Create a new ad set with broader targeting to test new audiences")
                    print("Action: test_new_audiences -> suggestion logged")

                elif opt == 'refresh_ad_creative':
                    results.append("Suggestion: Create fresh ad creative with new images/copy to improve CTR")
                    print("Action: refresh_ad_creative -> suggestion logged")

                elif opt == 'optimize_targeting':
                    results.append("Suggestion: Refine targeting based on best-performing demographics")
                    print("Action: optimize_targeting -> suggestion logged")

                elif opt == 'improve_ad_copy':
                    results.append("Suggestion: Use AI to generate improved ad copy variations")
                    print("Action: improve_ad_copy -> suggestion logged")

                elif opt == 'scale_successful_elements' and current_budget:
                    new_budget = min(current_budget * 1.3, 500)
                    self.meta_api.update_campaign(campaign_id, daily_budget=int(new_budget * 100))
                    results.append(f"Budget scaled: ${current_budget:.0f} → ${new_budget:.0f}/day")
                    print(f"Action: scale_successful_elements -> budget ${new_budget:.0f}/day")

                elif opt == 'expand_successful_targeting':
                    results.append("Suggestion: Expand targeting to lookalike audiences to reach more people")
                    print("Action: expand_successful_targeting -> suggestion logged")

                elif opt == 'duplicate_successful_creative':
                    results.append("Suggestion: Duplicate this campaign with different creative variations to A/B test")
                    print("Action: duplicate_successful_creative -> suggestion logged")

                else:
                    results.append(f"Info: {opt} - no action taken")
                    print(f"Action: {opt} -> no action taken")
            except Exception as e:
                results.append(f"Error in {opt}: {e}")
                print(f"Action: {opt} -> error: {e}")
        return results


# ==================== UNIVERSAL META ADS AGENT ====================

class UniversalMetaAdsAgent:
    def __init__(self, meta_credentials, ai_api_key=None, groq_api_key=None, gemini_api_key=None,
                 tenant_id='default', safety_guard_instance=None, campaigns_file=None):
        self.tenant_id = tenant_id
        # Each tenant gets its OWN safety guard (own spend cap, own audit log file)
        # so one studio's campaigns can never eat into another studio's budget cap.
        # Falls back to the shared global one for 100% backward compatibility with
        # single-tenant setups (this is exactly what happens for 'default').
        self.safety_guard = safety_guard_instance or safety_guard

        self.meta_api = MetaAPI(
            meta_credentials['access_token'],
            meta_credentials['ad_account_id'],
            meta_credentials.get('app_id'),
            meta_credentials.get('app_secret'),
            page_token=meta_credentials.get('page_token')
        )
        # Initialize OpenAI if available
        self.openai_client = None
        try:
            import openai
            if ai_api_key:
                openai.api_key = ai_api_key
                self.openai_client = openai
            elif groq_api_key:
                openai.api_key = groq_api_key
                self.openai_client = openai
            elif gemini_api_key:
                # For Google Gemini, we'll use a different approach
                import requests
                self.gemini_api_key = gemini_api_key
        except ImportError:
            pass
        
        self.ai_strategy_engine = AIStrategyEngine(ai_api_key)
        self.performance_optimizer = PerformanceOptimizer(self.meta_api, safety_guard_instance=self.safety_guard)
        self.audience_analyzer = AudienceAnalyzer()
        self.campaigns_file = campaigns_file or os.path.join(os.path.dirname(__file__), 'campaigns.json')
        self.campaigns = self._load_campaigns()
        self.performance_optimizer.campaigns_ref = self.campaigns  # live reference for safety-cap checks
        # Enrich with Meta data on startup
        self._sync_from_meta()

    def _save_campaigns(self):
        try:
            with open(self.campaigns_file, 'w') as f:
                json.dump(self.campaigns, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving campaigns: {e}")

    def _load_campaigns(self):
        try:
            if os.path.exists(self.campaigns_file):
                with open(self.campaigns_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading campaigns: {e}")
        return {}

    def _sync_from_meta(self):
        try:
            meta_camps = self.meta_api.get_campaigns(limit=50)
            for mc in meta_camps:
                cid = mc.get('id')
                if cid and cid not in self.campaigns:
                    self.campaigns[cid] = {
                        'campaign_id': cid,
                        'campaign_name': mc.get('name'),
                        'status': mc.get('status', 'unknown'),
                        'created_at': mc.get('created_time', ''),
                        'meta_response': mc
                    }
            if meta_camps:
                self._save_campaigns()
        except Exception as e:
            print(f"Error syncing from Meta: {e}")

    def _accounts_file(self):
        return os.path.join(os.path.dirname(__file__), 'accounts.json')

    def get_accounts(self):
        try:
            if os.path.exists(self._accounts_file()):
                with open(self._accounts_file()) as f:
                    return json.load(f)
        except: pass
        return {}

    def save_account(self, account_data):
        accounts = self.get_accounts()
        aid = account_data.get('id') or str(int(time.time()))
        account_data['id'] = aid
        accounts[aid] = account_data
        try:
            with open(self._accounts_file(), 'w') as f:
                json.dump(accounts, f, indent=2)
            return aid
        except Exception as e:
            return None

    def delete_account(self, account_id):
        accounts = self.get_accounts()
        if account_id in accounts:
            del accounts[account_id]
            with open(self._accounts_file(), 'w') as f:
                json.dump(accounts, f, indent=2)
            return True
        return False

    def create_campaign_for_business(self, business_profile):
        business_analysis = self.ai_strategy_engine.analyze_business(business_profile)
        strategy = self.ai_strategy_engine.generate_strategy(business_analysis)

        daily_budget = business_profile.get('budget', {}).get('daily', 10)

        # SAFETY GUARD: never call the real Meta API until we've confirmed this
        # won't push total daily spend across all campaigns past the hard cap.
        allowed, reason = self.safety_guard.check_new_budget(self.campaigns, daily_budget, action='create_campaign')
        if not allowed:
            return {
                'campaign_id': None,
                'error': {'message': f"Blocked by safety cap: {reason}"},
                'strategy': strategy,
                'blocked_by_safety_guard': True
            }

        campaign_result = self.meta_api.create_campaign({
            'name': strategy['campaign_name'],
            'objective': strategy['objectives'][0] if strategy['objectives'] else 'OUTCOME_LEADS',
            'daily_budget': daily_budget,
        })

        if 'error' in campaign_result:
            campaign_id = f"local_{int(time.time())}"
            self.campaigns[campaign_id] = {
                'campaign_id': campaign_id,
                'business_name': business_profile.get('business_name', 'Unknown'),
                'industry': business_profile.get('industry', 'service'),
                'strategy': strategy,
                'adset_id': None, 'ad_id': None,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'status': f"error: {campaign_result['error'].get('message', 'Unknown error')[:50]}",
                'media_file': business_profile.get('media_file') or business_profile.get('media_url'),
                'media_urls': business_profile.get('media_urls', []),
                'headline': business_profile.get('headline'),
                'message': business_profile.get('message'),
                'cta': business_profile.get('cta'),
                'destination_url': business_profile.get('destination_url'),
                'radius': business_profile.get('radius'),
                'age_min': business_profile.get('age_min'),
                'age_max': business_profile.get('age_max'),
                'location': business_profile.get('location'),
                'budget_daily': business_profile.get('budget', {}).get('daily'),
                'meta_response': campaign_result
            }
            return {'campaign_id': campaign_id, 'error': campaign_result['error'], 'strategy': strategy}

        campaign_id = campaign_result.get('id')

        self.campaigns[campaign_id] = {
            'campaign_id': campaign_id,
            'business_name': business_profile.get('business_name', 'Unknown'),
            'industry': business_profile.get('industry', 'service'),
            'strategy': strategy,
            'adset_id': None, 'ad_id': None,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'status': 'active',
            'media_file': business_profile.get('media_file') or business_profile.get('media_url'),
            'media_urls': business_profile.get('media_urls', []),
            'headline': business_profile.get('headline'),
            'message': business_profile.get('message'),
            'cta': business_profile.get('cta'),
            'destination_url': business_profile.get('destination_url'),
            'radius': business_profile.get('radius'),
            'age_min': business_profile.get('age_min'),
            'age_max': business_profile.get('age_max'),
            'location': business_profile.get('location'),
            'budget_daily': business_profile.get('budget', {}).get('daily'),
            'gender': business_profile.get('gender', 'all'),
            'interests': business_profile.get('interests', []),
            'has_children': business_profile.get('has_children', ''),
            'platforms': business_profile.get('platforms', 'both'),
            'placements': business_profile.get('placements', []),
            'fb_budget_pct': business_profile.get('fb_budget_pct', 60),
            'meta_response': campaign_result
        }
        self._save_campaigns()

        page_id = self.meta_api.get_page_id()
        location = business_profile.get('location', '')
        radius = business_profile.get('radius', 25)
        age_min = business_profile.get('age_min', 18)
        age_max = business_profile.get('age_max', 65)
        zip_match = re.search(r'\b(\d{5})(?:-\d{4})?\b', location)
        if zip_match:
            zips_found = re.findall(r'\b(\d{5})(?:-\d{4})?\b', location)
            geo_locations = {'zips': [{'key': f'US:{z}'} for z in zips_found], 'location_types': ['home', 'recent']}
        else:
            geo_locations = {'countries': ['US'], 'location_types': ['home', 'recent']}
        targeting = {'geo_locations': geo_locations, 'age_min': age_min, 'age_max': age_max, 'targeting_automation': {'advantage_audience': 0}}
        gender = business_profile.get('gender', 'all')
        if gender in ('male', 'female'):
            targeting['genders'] = [1 if gender == 'male' else 2]
        interests = business_profile.get('interests', [])
        has_children = business_profile.get('has_children', '')
        flexible_groups = []
        if interests:
            flexible_groups.append({'interests': [{'id': i} for i in interests]})
        if has_children == 'yes':
            flexible_groups.append({'family_statuses': [{'id': '6002714398372'}]})
        if flexible_groups:
            targeting['flexible_spec'] = flexible_groups
        if has_children == 'no':
            targeting['excluded_flexible_spec'] = [{'family_statuses': [{'id': '6002714398372'}]}]
        platforms = business_profile.get('platforms', 'both')
        placements = business_profile.get('placements', [])
        if page_id:
            if platforms == 'facebook':
                targeting['publisher_platforms'] = ['facebook']
            elif platforms == 'instagram':
                targeting['publisher_platforms'] = ['instagram']
            else:
                targeting['publisher_platforms'] = ['facebook', 'instagram']
        if placements and platforms != 'instagram':
            fb_positions = [p for p in placements if p in ('feed','story','marketplace','video_feeds','right_hand_column','search','instream_video')]
            if fb_positions:
                targeting['facebook_positions'] = fb_positions
        if placements and platforms != 'facebook':
            ig_positions = [p for p in placements if p in ('stream','story','explore','reels')]
            if ig_positions:
                targeting['instagram_positions'] = ig_positions

        adset_result = self.meta_api.create_ad_set({
            'name': f"{strategy['campaign_name']} - Ad Set",
            'campaign_id': campaign_id,
            'optimization_goal': business_profile.get('optimization_goal', 'OUTCOME_LEADS'),
            'targeting': targeting,
            'page_id': page_id,
        })
        if 'error' not in adset_result:
            adset_id = adset_result.get('id')
            self.campaigns[campaign_id]['adset_id'] = adset_id
            self._save_campaigns()

            if page_id:
                dest_url = business_profile.get('destination_url') or business_profile.get('website', 'https://example.com')
                cta_type = business_profile.get('cta', 'LEARN_MORE')
                media_url = business_profile.get('media_url') or business_profile.get('media_file')
                media_urls = business_profile.get('media_urls', [])
                is_video = media_url and media_url.lower().endswith(('.mp4', '.mov', '.avi', '.webm'))
                if len(media_urls) > 1:
                    child_attachments = []
                    for mu in media_urls:
                        image_hash = self.meta_api.upload_image(mu)
                        if image_hash:
                            child_attachments.append({
                                'link': dest_url,
                                'name': business_profile.get('headline') or f"Special Offer - {business_profile.get('business_name', 'Your Business')}",
                                'description': business_profile.get('message') or f"Special Offer - {business_profile.get('business_name', 'Your Business')}. Experience great {business_profile.get('industry', '')} services!",
                                'image_hash': image_hash
                            })
                    if child_attachments:
                        creative = {
                            'object_story_spec': {
                                'page_id': page_id,
                                'link_data': {
                                    'link': dest_url,
                                    'child_attachments': child_attachments,
                                    'call_to_action': {'type': cta_type}
                                }
                            }
                        }
                    else:
                        creative = {'object_story_spec': {'page_id': page_id, 'link_data': {
                            'link': dest_url,
                            'message': business_profile.get('message') or f"Special Offer - {business_profile.get('business_name', 'Your Business')}",
                            'name': business_profile.get('headline') or f"Special Offer",
                            'call_to_action': {'type': cta_type}
                        }}}
                elif media_url and is_video:
                    video_id = self.meta_api.upload_video(media_url)
                    if video_id:
                        creative = {
                            'object_story_spec': {
                                'page_id': page_id,
                                'video_data': {
                                    'video_id': video_id,
                                    'message': business_profile.get('message') or f"Special Offer - {business_profile.get('business_name', 'Your Business')}. Experience great {business_profile.get('industry', '')} services!",
                                    'title': business_profile.get('headline') or f"Special Offer - {business_profile.get('business_name', 'Your Business')}",
                                    'call_to_action': {'type': cta_type},
                                    'link_url': dest_url
                                }
                            }
                        }
                    else:
                        creative = {'object_story_spec': {'page_id': page_id, 'link_data': {
                            'link': dest_url,
                            'message': business_profile.get('message') or f"Special Offer - ...",
                            'name': business_profile.get('headline') or f"Special Offer",
                            'call_to_action': {'type': cta_type}
                        }}}
                else:
                    link_data = {
                        'link': dest_url,
                        'message': business_profile.get('message') or f"Special Offer - {business_profile.get('business_name', 'Your Business')}. Experience great {business_profile.get('industry', '')} services!",
                        'name': business_profile.get('headline') or f"Special Offer - {business_profile.get('business_name', 'Your Business')}",
                        'call_to_action': {'type': cta_type}
                    }
                    if media_url and not is_video:
                        image_hash = self.meta_api.upload_image(media_url)
                        if image_hash:
                            link_data['image_hash'] = image_hash
                    creative = {'object_story_spec': {'page_id': page_id, 'link_data': link_data}}
                ad_result = self.meta_api.create_ad({
                    'name': f"{strategy['campaign_name']} - Ad",
                    'adset_id': adset_id,
                    'creative': creative
                })
                print(f"Ad creation result: {json.dumps(ad_result, indent=2)[:500]}")
                if 'error' not in ad_result:
                    self.campaigns[campaign_id]['ad_id'] = ad_result.get('id')
                    self.campaigns[campaign_id]['ad_status'] = 'active'
                elif ad_result.get('auth_required'):
                    self.campaigns[campaign_id]['ad_status'] = 'pending_auth'
                else:
                    self.campaigns[campaign_id]['ad_status'] = f"error: {ad_result.get('error', {}).get('message', 'Unknown')[:100]}"
                self._save_campaigns()

        self.performance_optimizer.start_monitoring(campaign_id)

        return {
            'campaign_id': campaign_id,
            'adset_id': self.campaigns[campaign_id].get('adset_id'),
            'ad_id': self.campaigns[campaign_id].get('ad_id'),
            'strategy': strategy,
            'meta_response': campaign_result
        }

    def get_all_campaigns(self):
        return list(self.campaigns.values())

    def get_campaign_performance(self, campaign_id):
        if campaign_id not in self.campaigns:
            return None
        campaign = self.campaigns[campaign_id]
        metrics = self.performance_optimizer.get_campaign_metrics(campaign_id)
        analysis = self.performance_optimizer.analyze_performance(metrics)
        return {
            'campaign': campaign,
            'metrics': metrics,
            'analysis': analysis
        }

    def optimize_campaigns(self):
        for campaign_id in self.campaigns:
            perf = self.get_campaign_performance(campaign_id)
            if perf:
                opts = self.performance_optimizer.generate_optimizations(perf['analysis'])
                self.performance_optimizer.apply_optimizations(opts, campaign_id)


# ==================== CONTENT SCHEDULER ====================

class ContentScheduler:
    def __init__(self, meta_api, page_id=None):
        self.meta_api = meta_api
        self.page_id = page_id
        self.posts_file = os.path.join(os.path.dirname(__file__), 'posts.json')
        self.posts = self._load_posts()

    def _load_posts(self):
        try:
            if os.path.exists(self.posts_file):
                with open(self.posts_file) as f:
                    return json.load(f)
        except: pass
        return {}

    def _save_posts(self):
        try:
            with open(self.posts_file, 'w') as f:
                json.dump(self.posts, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving posts: {e}")

    def create_post(self, data):
        post_id = f"post_{int(time.time() * 1000)}"
        post = {
            'id': post_id,
            'platform': data.get('platform', 'facebook'),
            'content_type': data.get('content_type', 'image'),
            'headline': data.get('headline', ''),
            'message': data.get('message', ''),
            'ai_instruction': data.get('ai_instruction', ''),
            'cta': data.get('cta', ''),
            'media_file': data.get('media_file', ''),
            'media_url': data.get('media_url', ''),
            'media_urls': data.get('media_urls', []),
            'media_files': data.get('media_files', []),
            'link_url': data.get('link_url', ''),
            'scheduled_time': data.get('scheduled_time'),
            'status': data.get('status', 'draft'),
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'meta_response': None
        }
        self.posts[post_id] = post
        self._save_posts()
        return post

    def update_post(self, post_id, data):
        if post_id not in self.posts:
            return None
        for k, v in data.items():
            if v is not None:
                self.posts[post_id][k] = v
        self._save_posts()
        return self.posts[post_id]

    def delete_post(self, post_id):
        if post_id in self.posts:
            self.posts[post_id]['status'] = 'trashed'
            self._save_posts()
            return True
        return False

    def restore_post(self, post_id):
        if post_id in self.posts and self.posts[post_id].get('status') == 'trashed':
            self.posts[post_id]['status'] = 'draft'
            self._save_posts()
            return True
        return False

    def delete_forever(self, post_id):
        if post_id in self.posts:
            post = self.posts[post_id]
            # Try to delete from Facebook/Instagram if it was published
            meta_response = post.get('meta_response') or {}
            platform = post.get('platform', 'facebook')
            fb_post_id = meta_response.get('post_id') or meta_response.get('id')
            if fb_post_id and post.get('status') == 'published':
                try:
                    if platform == 'instagram':
                        self.meta_api.delete_instagram_post(fb_post_id)
                    else:
                        self.meta_api.delete_facebook_post(fb_post_id)
                except Exception:
                    pass  # Don't block local delete if Meta API fails
            del self.posts[post_id]
            self._save_posts()
            return True
        return False

    def get_all_posts(self):
        return list(self.posts.values())

    def get_post(self, post_id):
        return self.posts.get(post_id)

    def publish_now(self, post_id):
        post = self.posts.get(post_id)
        if not post:
            return {'error': 'Post not found'}
        if post['status'] == 'published':
            return {'error': 'Already published'}
        result = self._execute_publish(post)
        if 'error' not in result:
            post['status'] = 'published'
            post['published_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            post['meta_response'] = result
            self._save_posts()
        return result

    def _execute_publish(self, post):
        page_id = self.page_id or self.meta_api.get_page_id()
        if not page_id:
            return {'error': 'No page ID available'}
        pt = self.meta_api.page_token or self.meta_api.access_token
        platform = post.get('platform', 'facebook')
        message = post.get('message', '')
        content_type = post.get('content_type', 'image')
        media_file = post.get('media_file', '')
        media_url = post.get('media_url', '')
        media_urls = post.get('media_urls', [])
        media_files = post.get('media_files', [])
        link_url = post.get('link_url', '')
        if link_url:
            message = f"{message}\n\n{link_url}" if message else link_url
        scheduled = post.get('scheduled_time')
        if platform == 'instagram':
            ig_id = self.meta_api.get_instagram_business_account_id(page_id)
            if not ig_id:
                return {'error': 'No Instagram Business Account linked to this page'}
            if content_type == 'carousel' and (media_urls or media_files):
                urls = media_urls if media_urls else media_files
                return self.meta_api.create_instagram_carousel_post(ig_id, urls, message, scheduled, page_id=page_id)
            elif media_url:
                return self.meta_api.create_instagram_post(ig_id, media_url, message, scheduled, page_id=page_id)
            elif media_file:
                return self.meta_api.create_instagram_post(ig_id, f"{os.path.dirname(__file__).replace(chr(92), '/')}/uploads/{os.path.basename(media_file)}", message, scheduled, page_id=page_id)
            else:
                return {'error': 'Instagram requires an image or video'}
        else:
            if content_type == 'carousel' and (media_urls or media_files):
                urls = media_urls if media_urls else media_files
                return self.meta_api.create_facebook_carousel_post(page_id, urls, message, scheduled, page_token=pt)
            elif content_type == 'video' and (media_url or media_file):
                video_path = media_file if media_file else media_url
                return self.meta_api.create_facebook_video_post(page_id, video_path, message, scheduled, page_token=pt)
            elif media_file:
                return self.meta_api.create_facebook_photo_post(page_id, media_file, message, page_token=pt)
            elif media_url:
                return self.meta_api.create_facebook_post(page_id, message, media_url, scheduled, page_token=pt)
            else:
                return self.meta_api.create_facebook_post(page_id, message, scheduled_time=scheduled, page_token=pt)


class SocialMediaAutoResponder:
    def __init__(self, meta_api, ai_engine=None):
        self.meta_api = meta_api
        self.ai_engine = ai_engine or AIStrategyEngine(meta_api)
        self.rules_file = os.path.join(os.path.dirname(__file__), 'responder_rules.json')
        self.rules = self._load_rules()
        self.responses_file = os.path.join(os.path.dirname(__file__), 'responder_log.json')
        self.responses = self._load_responses()
        self.enabled = True

    def _load_rules(self):
        try:
            if os.path.exists(self.rules_file):
                with open(self.rules_file) as f:
                    return json.load(f)
        except: pass
        return {'rules': [], 'default_response': 'Thank you for your comment!', 'use_ai': True}

    def _save_rules(self):
        try:
            with open(self.rules_file, 'w') as f:
                json.dump(self.rules, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving rules: {e}")

    def _load_responses(self):
        try:
            if os.path.exists(self.responses_file):
                with open(self.responses_file) as f:
                    return json.load(f)
        except: pass
        return {'responses': []}

    def _save_responses(self):
        try:
            with open(self.responses_file, 'w') as f:
                json.dump(self.responses, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving responses: {e}")

    def add_rule(self, keyword, response_template, platform='all', sentiment=None):
        rule_id = f"rule_{int(time.time())}_{len(self.rules['rules'])}"
        rule = {
            'id': rule_id,
            'keyword': keyword.lower(),
            'response_template': response_template,
            'platform': platform,
            'sentiment': sentiment,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'enabled': True
        }
        self.rules['rules'].append(rule)
        self._save_rules()
        return rule

    def delete_rule(self, rule_id):
        self.rules['rules'] = [r for r in self.rules['rules'] if r['id'] != rule_id]
        self._save_rules()

    def set_default_response(self, text):
        self.rules['default_response'] = text
        self._save_rules()

    def set_ai_mode(self, enabled):
        self.rules['use_ai'] = enabled
        self._save_rules()

    def get_page_comments(self, page_id=None, limit=50):
        pid = page_id or self.meta_api.get_page_id()
        if not pid:
            return []
        try:
            url = f"https://graph.facebook.com/v19.0/{pid}/feed?fields=message,from,created_time,comments&limit={limit}&access_token={self.meta_api.page_token or self.meta_api.access_token}"
            resp = requests.get(url)
            if resp.status_code == 200:
                data = resp.json()
                comments = []
                for post in data.get('data', []):
                    if 'comments' in post:
                        for c in post['comments'].get('data', []):
                            c['post_id'] = post.get('id')
                            c['post_message'] = post.get('message', '')
                            comments.append(c)
                return comments
        except Exception as e:
            print(f"Error fetching comments: {e}")
        return []

    def auto_respond_to_comment(self, comment_data, page_id=None):
        comment_id = comment_data.get('id')
        comment_message = comment_data.get('message', '')
        from_name = comment_data.get('from', {}).get('name', 'User')
        if not comment_id or not comment_message:
            return None
        if not self.enabled:
            return None
        response_text = None
        if self.rules.get('use_ai') and self.ai_engine:
            try:
                prompt = f"A user named {from_name} commented: \"{comment_message}\". Write a friendly, professional reply (max 2 sentences) as the page owner."
                ai_resp = self.ai_engine.generate_ad_copy_with_ai(prompt, count=1)
                if ai_resp and 'error' not in ai_resp:
                    response_text = ai_resp.get('variations', [ai_resp.get('text', '')])[0]
            except:
                pass
        if not response_text:
            for rule in self.rules['rules']:
                if not rule.get('enabled', True):
                    continue
                if rule['keyword'] in comment_message.lower():
                    response_text = rule['response_template']
                    break
        if not response_text:
            response_text = self.rules.get('default_response', 'Thank you for your comment!')
        try:
            pid = page_id or self.meta_api.get_page_id()
            if pid and comment_id:
                url = f"https://graph.facebook.com/v19.0/{comment_id}/comments?message={requests.utils.quote(response_text)}&access_token={self.meta_api.page_token or self.meta_api.access_token}"
                resp = requests.post(url)
                log_entry = {
                    'comment_id': comment_id,
                    'original_message': comment_message,
                    'response': response_text,
                    'from_name': from_name,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'success': resp.status_code == 200,
                    'meta_response': resp.json() if resp.status_code == 200 else resp.text
                }
                self.responses['responses'].append(log_entry)
                self._save_responses()
                if resp.status_code == 200:
                    return log_entry
                return None
        except Exception as e:
            print(f"Error posting reply: {e}")
        return None

    def auto_respond_all(self, page_id=None, limit=20):
        comments = self.get_page_comments(page_id, limit)
        results = []
        for c in comments:
            already = any(r['comment_id'] == c['id'] for r in self.responses['responses'])
            if not already:
                result = self.auto_respond_to_comment(c, page_id)
                if result:
                    results.append(result)
        return results

    def get_log(self, limit=50):
        logs = list(reversed(self.responses['responses']))
        return logs[:limit]


class MultiPlatformScheduler:
    def __init__(self, meta_api):
        self.meta_api = meta_api
        self.queue_file = os.path.join(os.path.dirname(__file__), 'multi_queue.json')
        self.queue = self._load_queue()
        self.thread = None
        self.running = False

    def _load_queue(self):
        try:
            if os.path.exists(self.queue_file):
                with open(self.queue_file) as f:
                    return json.load(f)
        except: pass
        return {'items': []}

    def _save_queue(self):
        try:
            with open(self.queue_file, 'w') as f:
                json.dump(self.queue, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving queue: {e}")

    def schedule_post(self, platforms, message, media_urls=None, scheduled_time=None, page_id=None, ig_id=None, content_type='image', link_url='', media_file='', media_files=None, headline='', ai_instruction='', cta=''):
        item_id = f"multi_{int(time.time())}_{len(self.queue['items'])}"
        item = {
            'id': item_id,
            'platforms': platforms if isinstance(platforms, list) else [platforms],
            'headline': headline,
            'message': message,
            'ai_instruction': ai_instruction,
            'cta': cta,
            'media_urls': media_urls or [],
            'scheduled_time': scheduled_time,
            'page_id': page_id,
            'ig_id': ig_id,
            'content_type': content_type,
            'link_url': link_url,
            'media_file': media_file,
            'media_files': media_files or [],
            'status': 'pending',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'results': {}
        }
        self.queue['items'].append(item)
        self._save_queue()
        return item

    def publish_to_facebook(self, item, page_id=None):
        pid = page_id or item.get('page_id') or self.meta_api.get_page_id()
        if not pid:
            return {'error': 'No page ID'}
        pt = self.meta_api.page_token or self.meta_api.access_token
        message = item['message']
        media = item.get('media_urls', [])
        scheduled = item.get('scheduled_time')
        if media:
            url = media[0]
            return self.meta_api.create_facebook_post(pid, message, url, scheduled, page_token=pt)
        return self.meta_api.create_facebook_post(pid, message, scheduled_time=scheduled, page_token=pt)

    def publish_to_instagram(self, item, page_id=None):
        pid = page_id or item.get('page_id') or self.meta_api.get_page_id()
        if not pid:
            return {'error': 'No page ID'}
        ig_id = item.get('ig_id') or self.meta_api.get_instagram_business_account_id(pid)
        if not ig_id:
            return {'error': 'No Instagram Business ID'}
        media = item.get('media_urls', [])
        message = item['message']
        scheduled = item.get('scheduled_time')
        if media:
            return self.meta_api.create_instagram_post(ig_id, media[0], message, scheduled, page_id=pid)
        return {'error': 'Instagram requires media'}

    def publish_item(self, item_id):
        item = None
        for q in self.queue['items']:
            if q['id'] == item_id:
                item = q
                break
        if not item:
            return {'error': 'Item not found'}
        results = {}
        for platform in item['platforms']:
            platform = platform.lower().strip()
            if platform in ('facebook', 'fb'):
                r = self.publish_to_facebook(item)
                results['facebook'] = r
            elif platform in ('instagram', 'ig'):
                r = self.publish_to_instagram(item)
                results['instagram'] = r
            elif platform == 'twitter':
                results['twitter'] = {'error': 'Twitter API not configured'}
            elif platform == 'linkedin':
                results['linkedin'] = {'error': 'LinkedIn API not configured'}
            else:
                results[platform] = {'error': f'Unknown platform: {platform}'}
        item['results'] = results
        all_success = all('error' not in r for r in results.values())
        item['status'] = 'published' if all_success else 'partial'
        self._save_queue()
        return results

    def publish_pending(self):
        now = datetime.now()
        results = []
        for item in self.queue['items']:
            if item['status'] != 'pending':
                continue
            sched = item.get('scheduled_time')
            if sched:
                try:
                    sched_dt = datetime.strptime(sched, '%Y-%m-%d %H:%M')
                    if sched_dt <= now:
                        r = self.publish_item(item['id'])
                        results.append({'id': item['id'], 'result': r})
                except:
                    pass
            else:
                r = self.publish_item(item['id'])
                results.append({'id': item['id'], 'result': r})
        return results

    def get_queue(self):
        return list(reversed(self.queue['items']))

    def get_item(self, item_id):
        for q in self.queue['items']:
            if q['id'] == item_id:
                return q
        return None

    def delete_item(self, item_id):
        self.queue['items'] = [q for q in self.queue['items'] if q['id'] != item_id]
        self._save_queue()

    def start_auto_publish(self, interval=60):
        self.running = True
        def _loop():
            while self.running:
                try:
                    self.publish_pending()
                except Exception as e:
                    print(f"Auto-publish error: {e}")
                time.sleep(interval)
        self.thread = threading.Thread(target=_loop, daemon=True)
        self.thread.start()

    def stop_auto_publish(self):
        self.running = False


class AdvancedLeadManagement:
    def __init__(self, meta_api, ai_engine=None):
        self.meta_api = meta_api
        self.ai_engine = ai_engine
        self.leads_file = os.path.join(os.path.dirname(__file__), 'leads.json')
        self.workflows_file = os.path.join(os.path.dirname(__file__), 'workflows.json')
        self.leads = self._load_leads()
        self.workflows = self._load_workflows()

    def _load_leads(self):
        try:
            if os.path.exists(self.leads_file):
                with open(self.leads_file) as f:
                    return json.load(f)
        except: pass
        return {'leads': [], 'score_config': {}}

    def _save_leads(self):
        try:
            with open(self.leads_file, 'w') as f:
                json.dump(self.leads, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving leads: {e}")

    def _load_workflows(self):
        try:
            if os.path.exists(self.workflows_file):
                with open(self.workflows_file) as f:
                    return json.load(f)
        except: pass
        return {'workflows': []}

    def _save_workflows(self):
        try:
            with open(self.workflows_file, 'w') as f:
                json.dump(self.workflows, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving workflows: {e}")

    def fetch_meta_leads(self, ad_id=None, limit=50):
        try:
            params = f"limit={limit}&access_token={self.meta_api.access_token}"
            if ad_id:
                url = f"https://graph.facebook.com/v19.0/{ad_id}/leads?{params}"
            else:
                url = f"https://graph.facebook.com/v19.0/act_{self.meta_api.ad_account_id.replace('act_','')}/leads?{params}"
            resp = requests.get(url)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                for lead in data:
                    self._import_meta_lead(lead)
                return data
        except Exception as e:
            print(f"Error fetching leads: {e}")
        return []

    def _import_meta_lead(self, meta_lead):
        lid = meta_lead.get('id')
        if not lid:
            return None
        if any(l.get('lead_id') == lid for l in self.leads['leads']):
            return None
        field_data = {}
        for f in meta_lead.get('field_data', []):
            name = f.get('name', '')
            values = f.get('values', [''])
            field_data[name] = values[0] if values else ''
        lead = {
            'lead_id': lid,
            'created_time': meta_lead.get('created_time', ''),
            'ad_id': meta_lead.get('ad_id', ''),
            'ad_name': meta_lead.get('ad_name', ''),
            'form_id': meta_lead.get('form_id', ''),
            'field_data': field_data,
            'name': field_data.get('full_name', field_data.get('name', 'Unknown')),
            'email': field_data.get('email', field_data.get('email_address', '')),
            'phone': field_data.get('phone_number', field_data.get('phone', '')),
            'status': 'new',
            'score': 0,
            'notes': '',
            'imported_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'last_contacted': None,
            'workflow_id': None
        }
        self.leads['leads'].append(lead)
        self._save_leads()
        return lead

    def add_lead_manual(self, name, email, phone='', source='manual', notes='', field_data=None):
        lead = {
            'lead_id': f"lead_{int(time.time())}_{len(self.leads['leads'])}",
            'created_time': datetime.now().isoformat(),
            'ad_id': '',
            'ad_name': '',
            'form_id': '',
            'field_data': field_data or {},
            'name': name,
            'email': email,
            'phone': phone,
            'source': source,
            'status': 'new',
            'score': 0,
            'notes': notes,
            'imported_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'last_contacted': None,
            'workflow_id': None
        }
        self.leads['leads'].append(lead)
        self._save_leads()
        return lead

    def score_lead(self, lead_id):
        for lead in self.leads['leads']:
            if lead.get('lead_id') == lead_id:
                score = 0
                if lead.get('email'):
                    score += 20
                if lead.get('phone'):
                    score += 15
                if lead.get('name') and lead['name'] != 'Unknown':
                    score += 10
                fd = lead.get('field_data', {})
                for key in fd:
                    val = str(fd[key]).strip()
                    if val and len(val) > 5:
                        score += 5
                if lead.get('source') == 'manual':
                    score += 5
                if self.ai_engine:
                    try:
                        freq = sum(1 for l in self.leads['leads'] if l.get('email') == lead.get('email'))
                        if freq > 1:
                            score -= 10
                    except:
                        pass
                lead['score'] = min(score, 100)
                self._save_leads()
                return lead['score']
        return 0

    def update_lead_status(self, lead_id, status, notes=None):
        for lead in self.leads['leads']:
            if lead.get('lead_id') == lead_id:
                lead['status'] = status
                if notes is not None:
                    lead['notes'] = notes
                if status in ('contacted', 'converted'):
                    lead['last_contacted'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                self._save_leads()
                return lead
        return None

    def assign_workflow(self, lead_id, workflow_id):
        for lead in self.leads['leads']:
            if lead.get('lead_id') == lead_id:
                lead['workflow_id'] = workflow_id
                self._save_leads()
                return lead
        return None

    def create_workflow(self, name, steps):
        wid = f"wf_{int(time.time())}"
        workflow = {
            'id': wid,
            'name': name,
            'steps': steps,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'enabled': True
        }
        self.workflows['workflows'].append(workflow)
        self._save_workflows()
        return workflow

    def delete_workflow(self, workflow_id):
        self.workflows['workflows'] = [w for w in self.workflows['workflows'] if w['id'] != workflow_id]
        self._save_workflows()

    def process_workflow_step(self, lead, step):
        step_type = step.get('type', '')
        if step_type == 'email':
            print(f"[LeadMgmt] Would send email to {lead.get('email')}: {step.get('template', '')}")
        elif step_type == 'wait':
            print(f"[LeadMgmt] Wait step: {step.get('duration', '1d')}")
        elif step_type == 'update_status':
            lead['status'] = step.get('status', 'contacted')
        elif step_type == 'add_note':
            existing = lead.get('notes', '')
            lead['notes'] = f"{existing}\\n{step.get('note', '')}" if existing else step.get('note', '')
        return lead

    def run_workflows(self):
        results = []
        now = datetime.now()
        for lead in self.leads['leads']:
            wf_id = lead.get('workflow_id')
            if not wf_id:
                continue
            workflow = None
            for w in self.workflows['workflows']:
                if w['id'] == wf_id and w.get('enabled', True):
                    workflow = w
                    break
            if not workflow:
                continue
            for step in workflow.get('steps', []):
                self.process_workflow_step(lead, step)
            results.append({'lead_id': lead.get('lead_id'), 'workflow': workflow.get('name')})
        self._save_leads()
        return results

    def get_leads(self, status=None, limit=100):
        leads = self.leads['leads']
        if status:
            leads = [l for l in leads if l.get('status') == status]
        return sorted(leads, key=lambda x: x.get('imported_at', ''), reverse=True)[:limit]

    def get_lead_stats(self):
        total = len(self.leads['leads'])
        by_status = {}
        for l in self.leads['leads']:
            s = l.get('status', 'unknown')
            by_status[s] = by_status.get(s, 0) + 1
        avg_score = sum(l.get('score', 0) for l in self.leads['leads']) / max(total, 1)
        return {
            'total': total,
            'by_status': by_status,
            'average_score': round(avg_score, 1),
            'score_config': self.leads.get('score_config', {})
        }

    def delete_lead(self, lead_id):
        self.leads['leads'] = [l for l in self.leads['leads'] if l.get('lead_id') != lead_id]
        self._save_leads()


# ==================== WEB INTERFACE ====================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Meta Ads Agent Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #f0f2f5; color: #1c1e21; }
  .topbar { background: #1877f2; color: white; padding: 14px 24px; display: flex; align-items: center; gap: 12px; }
  .topbar h1 { font-size: 1.3rem; }
  .topbar select { margin-left: auto; padding: 6px 12px; border-radius: 6px; border: none; font-size: .85rem; }
  .nav-tabs { display: flex; gap: 2px; background: white; border-radius: 8px; padding: 3px; margin: 16px 0; box-shadow: 0 1px 4px rgba(0,0,0,.12); }
  .nav-tab { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: .85rem; font-weight: 600; background: transparent; color: #65676b; transition: all .2s; }
  .nav-tab.active { background: #1877f2; color: white; }
  .nav-tab:hover:not(.active) { background: #f0f2f5; }
  .page { display: none; }
  .page.active { display: block; }
  .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .stat-card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.12); }
  .stat-card .num { font-size: 2rem; font-weight: 700; color: #1877f2; }
  .stat-card .label { color: #65676b; font-size: .85rem; margin-top: 4px; }
  .section { background: white; border-radius: 10px; padding: 24px; box-shadow: 0 1px 4px rgba(0,0,0,.12); margin-bottom: 20px; }
  .section h2 { font-size: 1.1rem; margin-bottom: 16px; color: #1c1e21; }
  .btn { display: inline-block; padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: .9rem; font-weight: 600; transition: opacity .2s; }
  .btn:hover { opacity: .85; }
  .btn-primary { background: #1877f2; color: white; }
  .btn-success { background: #42b72a; color: white; }
  .btn-warn { background: #f5a623; color: white; }
  .btn-danger { background: #dc2626; color: white; }
  .btn-danger:hover { background: #b91c1c; }
  .btn-sm { padding: 6px 14px; font-size: .8rem; }
  .btn-danger { background: #fa3e3e; color: white; }
  .btn-outline { background: transparent; border: 1px solid #1877f2; color: #1877f2; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 12px 10px; text-align: left; border-bottom: 1px solid #e4e6eb; font-size: .9rem; }
  th { font-weight: 600; color: #65676b; font-size: .8rem; text-transform: uppercase; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: .75rem; font-weight: 600; }
  .badge-active { background: #e7f7e1; color: #1a7f37; }
  .badge-good { background: #e7f0ff; color: #1877f2; }
  .badge-poor { background: #fce8e8; color: #c0392b; }
  .badge-warn { background: #fff8e1; color: #e67e22; }
  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 100; align-items: center; justify-content: center; }
  .modal-bg.open { display: flex; }
  .modal { background: white; border-radius: 12px; padding: 24px; width: 100%; max-width: 700px; max-height: 90vh; overflow-y: auto; }
  .modal h3 { margin-bottom: 16px; font-size: 1.1rem; }
  .form-group { margin-bottom: 14px; }
  .form-group label { display: block; margin-bottom: 4px; font-size: .82rem; font-weight: 600; color: #65676b; }
  .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 9px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: .9rem; }
  .form-group textarea { resize: vertical; min-height: 60px; }
  .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .alert { padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-size: .9rem; }
  .alert-success { background: #e7f7e1; color: #1a7f37; border-left: 4px solid #42b72a; }
  .alert-error { background: #fce8e8; color: #c0392b; border-left: 4px solid #fa3e3e; }
  .alert-info { background: #e7f0ff; color: #1877f2; border-left: 4px solid #1877f2; }
  #toast { position: fixed; bottom: 30px; right: 30px; background: #333; color: white; padding: 14px 22px;
           border-radius: 8px; font-size: .9rem; opacity: 0; transition: opacity .3s; z-index: 999; pointer-events: none; }
  #toast.show { opacity: 1; }
  .detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .detail-box { background: #f0f2f5; border-radius: 8px; padding: 14px; }
  .detail-box .val { font-size: 1.3rem; font-weight: 700; color: #1877f2; }
  .detail-box .key { font-size: .78rem; color: #65676b; margin-top: 2px; }
  .tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 2px solid #e4e6eb; }
  .tab { padding: 8px 16px; cursor: pointer; border-radius: 6px 6px 0 0; font-size: .85rem; font-weight: 600; color: #65676b; }
  .tab.active { background: white; color: #1877f2; border-bottom: 2px solid #1877f2; margin-bottom: -2px; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .step-indicator { display: flex; gap: 4px; margin-bottom: 20px; }
  .step-indicator .step { flex: 1; text-align: center; padding: 8px; border-radius: 6px; font-size: .78rem; font-weight: 600; background: #e4e6eb; color: #65676b; cursor: pointer; }
  .step-indicator .step.active { background: #1877f2; color: white; }
  .step-indicator .step.done { background: #42b72a; color: white; }
  .step-content { display: none; }
  .step-content:first-child { display: block; }
  .upload-zone { border: 2px dashed #ddd; border-radius: 8px; padding: 30px; text-align: center; cursor: pointer; transition: border-color .2s; }
  .upload-zone:hover { border-color: #1877f2; }
  .upload-zone.has-file { border-color: #42b72a; background: #f0faf0; padding: 12px; }
  .preview-card { max-width: 340px; border: 1px solid #ddd; border-radius: 10px; overflow: hidden; background: white; margin: 0 auto; }
  .preview-card .preview-header { padding: 10px 12px; display: flex; align-items: center; gap: 8px; }
  .preview-card .preview-header .avatar { width: 36px; height: 36px; border-radius: 50%; background: #1877f2; display: flex; align-items: center; justify-content: center; color: white; font-weight: 700; font-size: .8rem; }
  .preview-card .preview-header .page-name { font-size: .85rem; font-weight: 600; }
  .preview-card .preview-header .sponsored { font-size: .7rem; color: #65676b; text-transform: uppercase; }
  .preview-card .preview-body { padding: 12px; }
  .preview-card .preview-title { font-weight: 600; font-size: .9rem; margin-bottom: 4px; }
  .preview-card .preview-desc { font-size: .82rem; color: #1c1e21; margin-bottom: 8px; }
  .preview-card .preview-cta { background: #e7f0ff; color: #1877f2; text-align: center; padding: 10px; font-weight: 700; font-size: .85rem; display: block; text-decoration: none; cursor: pointer; }
  .preview-card .preview-cta:hover { background: #d4e3ff; }
  .preview-card .preview-target { font-size: .75rem; color: #65676b; padding: 8px 12px; border-top: 1px solid #eee; }
  .slider-container { display: flex; align-items: center; gap: 12px; }
  .slider-container input[type=range] { flex: 1; }
  .slider-container .slider-val { font-weight: 700; color: #1877f2; min-width: 50px; }
  .cal-day-box { background: #fff; border: 2px solid #e4e6eb; border-radius: 8px; padding: 4px; min-height: 80px; font-size: .75rem; position: relative; }
  .cal-day-box .day-num { font-weight: 700; color: #65676b; margin-bottom: 2px; }
  .cal-day-box.other-month { opacity: .35; }
  .cal-day-box.today { border-color: #1877f2; background: #e7f3ff; }
  .cal-post-chip { display: block; padding: 2px 4px; border-radius: 4px; font-size: .65rem; margin-bottom: 2px; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cal-post-chip.facebook { background: #e7f3ff; color: #1877f2; border: 1px solid #1877f2; }
  .cal-post-chip.instagram { background: #fce4ec; color: #c62828; border: 1px solid #e91e63; }
  .cal-post-chip.draft { opacity: .6; }
  .cal-post-chip.published { opacity: 1; border-style: solid; }
  .cal-post-chip.scheduled { border-style: dashed; }
  .post-tab.active { background: #1877f2; color: #fff; border-color: #1877f2; }
  .campaign-filter-btn { padding:4px 12px; border:1px solid #ddd; border-radius:6px; background:#fff; cursor:pointer; font-size:.8rem; color:#65676b; }
  .campaign-filter-btn.active { background: #1877f2; color: #fff; border-color: #1877f2; }
</style>
</head>
<body>

<div class="topbar">
  <span style="font-size:1.5rem">&#x1f4ca;</span>
  <h1 data-i18n="title">Meta Ads Agent Dashboard</h1>
  <button id="lang-toggle" onclick="toggleLang()" style="background:#fff;border:1px solid #ddd;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:13px;font-weight:600;margin-left:10px;">ES/EN</button>
  <span style="margin-left:auto;font-size:.85rem;opacity:.85;" id="token-status"></span>
</div>

<div class="container">
  <div class="nav-tabs">
    <button class="nav-tab active" data-page="campaigns" data-i18n="campaigns_tab">Campaigns</button>
    <button class="nav-tab" data-page="content" data-i18n="content_tab">Content</button>
    <button class="nav-tab" data-page="leads" data-i18n="leads_tab">Leads</button>
    <button class="nav-tab" data-page="multiplatform" data-i18n="multiplatform_tab">Multi-Platform</button>
    <button class="nav-tab" data-page="responder" data-i18n="responder_tab">Auto Responder</button>
    <button class="nav-tab" data-page="reports" data-i18n="reports_tab">Reports</button>
    <button class="nav-tab" data-page="settings" data-i18n="settings_tab">Settings</button>
  </div>

  <div id="alert-area"></div>

  <!-- ===== PAGE: CAMPAIGNS ===== -->
  <div class="page active" id="page-campaigns">

  <div class="grid" id="stats-row">
    <div class="stat-card"><div class="num" id="stat-campaigns">0</div><div class="label" data-i18n="total_campaigns">Total Campaigns</div></div>
    <div class="stat-card"><div class="num" id="stat-active">0</div><div class="label" data-i18n="active">Active</div></div>
    <div class="stat-card"><div class="num" id="stat-budget">$0</div><div class="label" data-i18n="total_budget">Total Budget</div></div>
    <div class="stat-card"><div class="num" id="stat-leads">0</div><div class="label" data-i18n="total_leads">Total Leads</div></div>
  </div>

  <!-- Actions + Wizard Toggle -->
  <div class="section">
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
      <button class="btn btn-primary" onclick="openCreateModal()" data-i18n="create_campaign">+ Create Campaign</button>
      <button class="btn btn-success" onclick="optimizeAll()" data-i18n="optimize_all">Optimize All</button>
      <button class="btn" onclick="loadCampaigns()" style="background:#e4e6eb;" data-i18n="refresh">Refresh</button>
    </div>
  </div>

  <!-- Campaigns Table -->
  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
      <h2 style="margin:0;" data-i18n="campaigns_tab">Campaigns</h2>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button class="btn btn-sm campaign-filter-btn active" data-filter="all" onclick="setCampaignFilter('all')" data-i18n="all">All</button>
        <button class="btn btn-sm campaign-filter-btn" data-filter="trashed" onclick="setCampaignFilter('trashed')" data-i18n="trash">Trash</button>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th data-i18n="campaign_name">Campaign Name</th>
          <th data-i18n="ad_type">Ad Type</th>
          <th data-i18n="targeting">Targeting</th>
          <th data-i18n="budget">Budget</th>
          <th data-i18n="status">Status</th>
          <th data-i18n="created">Created</th>
          <th data-i18n="actions">Actions</th>
        </tr>
      </thead>
      <tbody id="campaigns-table">
        <tr><td colspan="7" style="text-align:center;color:#65676b;padding:30px;" data-i18n="no_campaigns">No campaigns yet. Create your first one!</td></tr>
      </tbody>
    </table>
    <div id="campaign-pagination" style="display:flex;align-items:center;justify-content:center;gap:8px;margin-top:12px;"></div>
  </div>

<!-- Leads Section -->
<div class="section" id="leads-section">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px;">
    <h2 style="margin:0;" data-i18n="leads">Leads</h2>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <select id="lead-form-select" style="padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:.85rem;">
        <option value="" data-i18n="select_form">Select a form...</option>
      </select>
      <button class="btn btn-sm btn-primary" onclick="loadLeads()" id="load-leads-btn" data-i18n="load_leads">Load Leads</button>
      <button class="btn btn-sm btn-success" id="lead-download-btn" style="display:none;" data-i18n="download_csv">Download CSV</button>
      <button class="btn btn-sm" onclick="loadLeadForms()" style="background:#e4e6eb;" data-i18n="refresh_forms">Refresh Forms</button>
    </div>
  </div>
  <div id="lead-no-forms" style="display:none;padding:20px;text-align:center;color:#65676b;" data-i18n="no_lead_forms">
    <span data-i18n="no_lead_forms">No lead forms found.</span> <a href="https://business.facebook.com/adsmanager/leadgen" target="_blank" data-i18n="create_one">Create one in Ads Manager</a>
  </div>
  <div id="leads-empty" style="display:none;padding:20px;text-align:center;color:#65676b;" data-i18n="no_leads">No leads yet.</div>
  <div id="leads-table-container"></div>
</div>
</div>

<!-- ===== PAGE: REPORTS ===== -->
<div class="page" id="page-reports">
  <div class="section">
    <h2 data-i18n="reports_tab">Reports Overview</h2>
    <div class="grid" id="reports-stats">
      <div class="stat-card"><div class="num" id="rpt-campaigns">0</div><div class="label" data-i18n="campaigns_tab">Campaigns</div></div>
      <div class="stat-card"><div class="num" id="rpt-spend">$0</div><div class="label" data-i18n="total_spend">Total Spend</div></div>
      <div class="stat-card"><div class="num" id="rpt-leads">0</div><div class="label" data-i18n="leads">Total Leads</div></div>
      <div class="stat-card"><div class="num" id="rpt-cpl">$0</div><div class="label" data-i18n="cost_per_lead">Cost / Lead</div></div>
      <div class="stat-card"><div class="num" id="rpt-ctr">0%</div><div class="label" data-i18n="ctr">CTR</div></div>
      <div class="stat-card"><div class="num" id="rpt-cpc">$0</div><div class="label" data-i18n="cpc">CPC</div></div>
    </div>
  </div>
  <div class="section">
    <h2 data-i18n="campaign_performance">Campaign Performance</h2>
    <table>
      <thead><tr><th data-i18n="campaign_name">Campaign</th><th data-i18n="spend">Spend</th><th data-i18n="clicks">Clicks</th><th data-i18n="impressions">Impressions</th><th data-i18n="leads">Leads</th><th data-i18n="cost_per_lead">Cost/Lead</th><th data-i18n="ctr">CTR</th></tr></thead>
      <tbody id="reports-table"><tr><td colspan="7" style="text-align:center;color:#65676b;padding:30px;"><span data-i18n="loading">Loading...</span></td></tr></tbody>
    </table>
  </div>
</div>

<!-- ===== PAGE: SETTINGS ===== -->
<div class="page" id="page-settings">
  <div class="section">
    <h2 data-i18n="ad_accounts">Ad Accounts</h2>
    <p style="font-size:.85rem;color:#65676b;margin-bottom:12px;" data-i18n="manage_accounts">Manage multiple ad accounts for different clients.</p>
    <div id="accounts-list"></div>
    <div style="margin-top:12px;padding:16px;background:#f0f2f5;border-radius:8px;">
      <h3 style="font-size:.9rem;margin-bottom:8px;" data-i18n="add_account">Add Account</h3>
      <input type="text" id="acc-name" data-i18n-placeholder="account_name" placeholder="Account name (e.g. Dojo 2 Ads)" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;margin-bottom:8px;">
      <input type="text" id="acc-token" data-i18n-placeholder="access_token" placeholder="Access Token" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;margin-bottom:8px;">
      <input type="text" id="acc-ad-account" data-i18n-placeholder="ad_account_id" placeholder="Ad Account ID (e.g. act_...)" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;margin-bottom:8px;">
      <input type="text" id="acc-app-id" data-i18n-placeholder="app_id" placeholder="App ID (optional)" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;margin-bottom:8px;">
      <input type="text" id="acc-app-secret" data-i18n-placeholder="app_secret" placeholder="App Secret (optional)" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;margin-bottom:8px;">
      <button class="btn btn-primary btn-sm" onclick="saveAccount()" data-i18n="save">Save Account</button>
    </div>
  </div>
  <div class="section">
    <h2 data-i18n="meta_pixel">Meta Pixel</h2>
    <p style="font-size:.85rem;color:#65676b;margin-bottom:8px;" data-i18n="pixel_desc">Optional: Add your Meta Pixel ID for conversion tracking.</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <input type="text" id="pixel-id" data-i18n-placeholder="pixel_id" placeholder="Pixel ID" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">
      <input type="number" id="lead-value" placeholder="Lead Value ($)" value="50" min="1" style="width:120px;padding:8px;border:1px solid #ddd;border-radius:6px;" title="Estimated value per lead for ROAS calculation">
      <button class="btn btn-primary btn-sm" onclick="savePixel()" data-i18n="save_pixel">Save Pixel</button>
    </div>
    <p style="font-size:11px;color:#888;margin-top:4px;" data-i18n="lead_value_desc">Lead Value: estimated revenue per lead, used for ROAS calculations.</p>
  </div>
  <div class="section" style="border:2px solid #f59e0b;border-radius:12px;padding:20px;">
    <h2 style="color:#d97706;margin-bottom:8px;">&#x1f6e1; Safety Guard</h2>
    <p style="font-size:.85rem;color:#65676b;margin-bottom:12px;">Control your daily spending limits. The optimizer will never exceed these limits.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div>
        <label style="font-size:12px;font-weight:600;color:#374151;">Max Daily Budget ($)</label>
        <div style="display:flex;gap:6px;margin-top:4px;">
          <input type="number" id="safety-max-budget" min="1" value="50" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">
          <button class="btn btn-sm btn-primary" onclick="updateSafetyBudget()" data-i18n="save">Save</button>
        </div>
      </div>
      <div>
        <label style="font-size:12px;font-weight:600;color:#374151;">Auto Budget Increase</label>
        <div style="display:flex;align-items:center;gap:10px;margin-top:6px;">
          <label style="position:relative;display:inline-block;width:44px;height:24px;">
            <input type="checkbox" id="safety-auto-budget" onchange="toggleAutoBudget()" style="opacity:0;width:0;height:0;">
            <span style="position:absolute;cursor:pointer;inset:0;background:#ccc;border-radius:12px;transition:.3s;" id="auto-budget-slider"></span>
          </label>
          <span id="auto-budget-label" style="font-size:13px;color:#65676b;">OFF</span>
        </div>
        <p style="font-size:11px;color:#888;margin-top:4px;">When ON, the optimizer can automatically increase budgets on performing campaigns.</p>
      </div>
    </div>
    <div id="safety-status-bar" style="margin-top:12px;padding:10px;background:#f0f2f5;border-radius:8px;font-size:12px;color:#374151;">
      Loading...
    </div>
  </div>
  <div class="section" id="admin-panel" style="border:2px solid #2563eb;border-radius:12px;padding:20px;display:none;">
    <h2 style="color:#2563eb;margin-bottom:4px;">&#x1f464; Client Management (Admin)</h2>
    <p style="font-size:.85rem;color:#65676b;margin-bottom:16px;">Create and manage client accounts. Each client has their own isolated campaigns, leads, and Meta credentials.</p>
    <div id="tenants-list" style="margin-bottom:16px;"></div>
    <div style="padding:16px;background:#f0f2f5;border-radius:8px;">
      <h3 style="font-size:.9rem;margin-bottom:8px;">Add New Client</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <input type="text" id="tenant-id" placeholder="Client ID (e.g. dojo-2)" style="padding:8px;border:1px solid #ddd;border-radius:6px;" title="Unique identifier, no spaces">
        <input type="text" id="tenant-name" placeholder="Client Name (e.g. Dojo 2)" style="padding:8px;border:1px solid #ddd;border-radius:6px;">
        <input type="password" id="tenant-password" placeholder="Password" style="padding:8px;border:1px solid #ddd;border-radius:6px;">
        <select id="tenant-industry" style="padding:8px;border:1px solid #ddd;border-radius:6px;">
          <option value="martialarts">Martial Arts</option>
          <option value="service">Service</option>
          <option value="restaurant">Restaurant</option>
          <option value="retail">Retail</option>
          <option value="ecommerce">E-commerce</option>
          <option value="medical">Medical</option>
        </select>
        <input type="text" id="tenant-meta-token" placeholder="Meta Access Token" style="grid-column:span 2;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <input type="text" id="tenant-ad-account" placeholder="Ad Account ID (act_...)" style="padding:8px;border:1px solid #ddd;border-radius:6px;">
        <input type="text" id="tenant-page-token" placeholder="Page Token (optional)" style="padding:8px;border:1px solid #ddd;border-radius:6px;">
        <input type="number" id="tenant-budget" placeholder="Daily Budget Cap ($)" value="50" min="1" style="padding:8px;border:1px solid #ddd;border-radius:6px;">
      </div>
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center;">
        <button class="btn btn-success" onclick="createTenant()">Create Client</button>
        <span id="tenant-create-msg" style="font-size:12px;"></span>
      </div>
    </div>
    <div style="margin-top:12px;padding:12px;background:#fef3c7;border-radius:8px;font-size:12px;color:#92400e;">
      <strong>How it works:</strong> Each client logs in at your-domain.com/?tenant=CLIENT-ID with their password. They only see their own campaigns and data.
    </div>
  </div>
</div>

<!-- ===== PAGE: CONTENT ===== -->
<div class="page" id="page-content">
  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
      <h2 data-i18n="calendar">Content Calendar</h2>
      <div style="display:flex;gap:8px;">
        <button class="btn btn-primary" onclick="openCreatePostModal()" data-i18n="create_post">+ New Post</button>
        <button class="btn" onclick="loadPosts()" style="background:#e4e6eb;" data-i18n="refresh">Refresh</button>
      </div>
    </div>
  </div>

  <div class="section" style="overflow-x:auto;">
    <div id="calendar-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <button class="btn btn-sm" onclick="calPrevMonth()" style="background:#e4e6eb;" data-i18n="prev_month">&lt; Prev</button>
      <h3 id="cal-month-label" style="margin:0;"></h3>
      <button class="btn btn-sm" onclick="calNextMonth()" style="background:#e4e6eb;" data-i18n="next_month">Next &gt;</button>
    </div>
    <div id="calendar-grid" style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;min-width:600px;"></div>
  </div>

  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
      <h2 style="margin:0;" data-i18n="all_posts">All Posts</h2>
      <div id="post-count" style="font-size:13px;color:#65676b;"></div>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
      <div style="display:flex;gap:4px;flex-wrap:wrap;" id="post-tabs">
        <button class="btn btn-sm post-tab active" data-filter="all" onclick="setPostFilter('all')" data-i18n="todos">Todos</button>
        <button class="btn btn-sm post-tab" data-filter="published" onclick="setPostFilter('published')" data-i18n="published">Publicados</button>
        <button class="btn btn-sm post-tab" data-filter="draft" onclick="setPostFilter('draft')" data-i18n="drafts">Borradores</button>
        <button class="btn btn-sm post-tab" data-filter="scheduled" onclick="setPostFilter('scheduled')" data-i18n="scheduled">Programados</button>
        <button class="btn btn-sm post-tab" data-filter="trashed" onclick="setPostFilter('trashed')" data-i18n="trash">🗑 Papelera</button>
      </div>
      <select id="post-platform-filter" onchange="renderFilteredPosts()" style="padding:6px;border:1px solid #ddd;border-radius:6px;font-size:13px;">
        <option value="all" data-i18n="all_platforms">Todas las plataformas</option>
        <option value="facebook">Facebook</option>
        <option value="instagram">Instagram</option>
      </select>
      <input type="text" id="post-search" data-i18n-placeholder="search_posts" placeholder="Buscar posts..." oninput="renderFilteredPosts()" style="padding:6px;border:1px solid #ddd;border-radius:6px;font-size:13px;flex:1;min-width:150px;">
    </div>
    <table>
      <thead><tr><th data-i18n="type">Type</th><th data-i18n="platform">Platform</th><th data-i18n="message">Message</th><th data-i18n="schedule">Scheduled</th><th data-i18n="status">Status</th><th data-i18n="actions">Actions</th></tr></thead>
      <tbody id="posts-table">
        <tr><td colspan="6" style="text-align:center;color:#65676b;padding:30px;" data-i18n="no_posts">No posts yet.</td></tr>
      </tbody>
    </table>
    <div id="post-pagination" style="display:flex;justify-content:center;gap:8px;margin-top:12px;"></div>
  </div>
</div>

<!-- Create Post Modal -->
<div class="modal-bg" id="create-post-modal">
  <div class="modal" style="max-width:560px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 data-i18n="create_post">New Post</h3>
      <button onclick="closeCreatePostModal()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <div class="form-group">
      <label data-i18n="platform">Platform</label>
      <div style="display:flex;gap:16px;padding:10px 12px;border:1px solid #ddd;border-radius:6px;background:#f8f9fa;">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:600;color:#1877f2;">
          <input type="checkbox" id="cp-platform-fb" value="facebook" checked onchange="toggleCpMedia()" style="width:18px;height:18px;cursor:pointer;accent-color:#1877f2;">
          <span>📘 Facebook</span>
        </label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:600;color:#e1306c;">
          <input type="checkbox" id="cp-platform-ig" value="instagram" onchange="toggleCpMedia()" style="width:18px;height:18px;cursor:pointer;accent-color:#e1306c;">
          <span>📸 Instagram</span>
        </label>
      </div>
      <div id="cp-platform-warning" style="display:none;color:#dc2626;font-size:.8rem;margin-top:4px;">⚠️ Select at least one platform.</div>
    </div>
    <div class="form-group">
      <label data-i18n="headline">Headline</label>
      <div style="display:flex;gap:6px;">
        <input type="text" id="cp-headline" placeholder="e.g. Special Offer!" maxlength="40" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <button class="btn btn-sm btn-outline" onclick="generateCpCopy()" style="white-space:nowrap;flex-shrink:0;" data-i18n="gen_ai">Gen AI</button>
        <a href="https://chat.openai.com" target="_blank" class="btn btn-sm btn-outline" style="white-space:nowrap;flex-shrink:0;text-decoration:none;background:#10a37f;color:#fff;border-color:#10a37f;" data-i18n="chatgpt" title="Open ChatGPT">ChatGPT</a>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="primary_text">Primary Text</label>
      <div style="display:flex;gap:6px;align-items:flex-start;">
        <textarea id="cp-message" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:60px;" data-i18n-placeholder="write_post" placeholder="Write your post..."></textarea>
        <button class="btn btn-sm btn-outline" onclick="generateCpCopy()" style="white-space:nowrap;flex-shrink:0;" data-i18n="gen_ai">Gen AI</button>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="ai_instruction">AI Instruction</label>
      <textarea id="cp-ai-instruction" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;resize:vertical;min-height:50px;" data-i18n-placeholder="ai_instruction_placeholder" placeholder="Describe what to promote. AI will use this to generate unique headlines and text."></textarea>
    </div>
    <div class="form-group">
      <label data-i18n="cta">Call to Action</label>
      <select id="cp-cta" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <option value="LEARN_MORE" data-i18n="cta_learn_more">Learn More</option>
        <option value="SIGN_UP" data-i18n="cta_sign_up">Sign Up</option>
        <option value="CONTACT_US" data-i18n="cta_contact_us">Contact Us</option>
        <option value="BOOK_NOW" data-i18n="cta_book_now">Book Now</option>
        <option value="GET_OFFER" data-i18n="cta_get_offer">Get Offer</option>
        <option value="SUBSCRIBE" data-i18n="cta_subscribe">Subscribe</option>
      </select>
    </div>
    <div class="form-group" id="cp-link-group">
      <label data-i18n="dest_url">Destination URL</label>
      <input type="url" id="cp-link" placeholder="https://your-site.com/page" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
    </div>
    <div class="form-group">
      <label data-i18n="upload_media">Media</label>
      <div class="upload-zone" onclick="document.getElementById('cp-media-input').click()" style="border:2px dashed #ddd;border-radius:8px;padding:20px;text-align:center;cursor:pointer;">
        <div id="cp-upload-placeholder" style="color:#65676b;">
          <div style="font-size:2rem;margin-bottom:8px;">+</div>
          <div data-i18n="click_to_upload">Click to upload images (multiple allowed)</div>
          <div style="font-size:.75rem;margin-top:4px;" data-i18n="media_hint">Select multiple images for carousel, or one video</div>
        </div>
        <div id="cp-upload-preview" style="display:none;">
          <div id="cp-carousel-thumbs" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;"></div>
          <video id="cp-preview-video" style="max-height:120px;border-radius:6px;display:none;" controls></video>
          <div id="cp-preview-filename" style="font-size:.85rem;margin-top:6px;font-weight:600;"></div>
        </div>
      </div>
      <input type="file" id="cp-media-input" accept="image/*,video/*" style="display:none" multiple onchange="handleCpMediaUpload(this.files)">
      <div style="margin-top:6px;font-size:.8rem;color:#65676b;">
        <span><span data-i18n="images">Images</span>: <span id="cp-img-count">0</span> | </span>
        <a href="#" onclick="event.preventDefault();document.getElementById('cp-media-input').click();return false;" style="color:#1877f2;" data-i18n="add_more">Add more</a>
      </div>
    </div>
  </div>
  <div style="margin-bottom:16px;font-weight:600;font-size:.9rem;color:#1877f2;" data-i18n="quick_schedule">Quick Schedule</div>
  <div class="form-group" style="background:#f0f2f5;border-radius:8px;padding:12px;margin-bottom:12px;">
    <div style="font-size:.8rem;color:#65676b;margin-bottom:8px;" data-i18n="single_post_hint">Set one date &amp; time for THIS post.</div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
      <input type="date" id="cp-schedule-start-date" style="flex:1;min-width:140px;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:.85rem;">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <select id="cp-schedule-hour" style="padding:6px 8px;border:1px solid #ddd;border-radius:6px;font-size:.85rem;width:60px;">
          <option>12</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option><option>8</option><option>9</option><option>10</option><option>11</option>
        </select>
        <span style="font-weight:700;color:#65676b;font-size:.9rem;">:</span>
        <select id="cp-schedule-min" style="padding:6px 8px;border:1px solid #ddd;border-radius:6px;font-size:.85rem;width:65px;">
          <option>00</option><option>15</option><option>30</option><option>45</option>
        </select>
        <select id="cp-schedule-ampm" style="padding:6px 8px;border:1px solid #ddd;border-radius:6px;font-size:.85rem;width:65px;">
          <option value="AM">AM</option><option value="PM">PM</option>
        </select>
        <button class="btn btn-sm btn-primary" onclick="addCpScheduleManualTime()" style="height:34px;" data-i18n="add_time">+ Add</button>
      </div>
    </div>
    <div id="cp-schedule-confirm" style="display:none;align-items:center;gap:8px;background:#e6f4ea;border:1px solid #34a853;border-radius:6px;padding:8px 10px;margin-top:6px;font-size:.85rem;color:#1e7e34;">
      <span id="cp-schedule-confirm-text" style="flex:1;"></span>
      <span style="cursor:pointer;color:#dc2626;font-weight:700;" onclick="clearCpScheduleManualTime()" title="Remove" data-i18n-title="remove">✕</span>
    </div>
    <div id="cp-schedule-manual-empty" style="color:#65676b;font-size:.85rem;margin-top:6px;" data-i18n="no_time_yet">No time added yet. Pick date &amp; time and click "+ Add".</div>
    <div style="margin-top:14px;border-top:1px solid #dadde1;padding-top:10px;">
      <div style="font-size:.8rem;color:#65676b;margin-bottom:6px;" data-i18n="repeat_hint">Want to repeat this same post automatically (so you don't have to schedule it day by day)? Save it first, then choose how often:</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button class="btn btn-sm" onclick="sendCpToRepeat(7)" style="background:#e7f3ff;" data-i18n="preset_7d">7 Days</button>
        <button class="btn btn-sm" onclick="sendCpToRepeat(28)" style="background:#e7f3ff;" data-i18n="preset_weekly">4 Weeks</button>
        <button class="btn btn-sm" onclick="sendCpToRepeat(90)" style="background:#e7f3ff;" data-i18n="preset_monthly">3 Months</button>
        <button class="btn btn-sm" onclick="sendCpToRepeat(180)" style="background:#e7f3ff;" data-i18n="preset_3m">6 Months</button>
        <button class="btn btn-sm" onclick="sendCpToRepeat(365)" style="background:#e7f3ff;" data-i18n="preset_12m">12 Months</button>
      </div>
    </div>
  </div>

    <input type="hidden" id="cp-schedule" value="">
    <div class="form-group" style="display:flex;gap:8px;flex-wrap:wrap;">
      <button class="btn btn-primary" onclick="savePostAsDraft()" style="flex:1;" data-i18n="save_draft">Save as Draft</button>
      <button class="btn btn-success" onclick="schedulePost()" style="flex:1;" data-i18n="schedule_post">Schedule</button>
      <button class="btn btn-warn" onclick="publishPostNow()" style="flex:1;" data-i18n="publish_post">Publish Now</button>
    </div>
    <div id="cp-status" style="margin-top:12px;font-size:.85rem;text-align:center;"></div>
  </div>
</div>

<!-- Repeat Schedule Modal -->
<div class="modal-bg" id="repeat-modal">
  <div class="modal" style="max-width:620px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3><span data-i18n="repeat_schedule">Repeat Schedule</span></h3>
      <button onclick="closeRepeatModal()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <input type="hidden" id="rpt-source-id" value="">
    <input type="hidden" id="rpt-source-type" value="post">
    <div class="form-group">
      <label data-i18n="headline">Headline</label>
      <div style="display:flex;gap:6px;">
        <input type="text" id="rpt-headline" maxlength="40" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <button class="btn btn-sm btn-outline" onclick="generateRptCopy()" style="white-space:nowrap;flex-shrink:0;" data-i18n="gen_ai">Gen AI</button>
        <a href="https://chat.openai.com" target="_blank" class="btn btn-sm btn-outline" style="white-space:nowrap;flex-shrink:0;text-decoration:none;background:#10a37f;color:#fff;border-color:#10a37f;" data-i18n="chatgpt" title="Open ChatGPT">ChatGPT</a>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="primary_text">Primary Text</label>
      <div style="display:flex;gap:6px;align-items:flex-start;">
        <textarea id="rpt-message" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:60px;"></textarea>
        <button class="btn btn-sm btn-outline" onclick="generateRptCopy()" style="white-space:nowrap;flex-shrink:0;" data-i18n="gen_ai">Gen AI</button>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="ai_instruction">AI Instruction</label>
      <textarea id="rpt-ai-instruction" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:40px;" data-i18n-placeholder="ai_instruction_placeholder" placeholder="Describe what to promote. AI will use this to generate unique headlines and text."></textarea>
    </div>
    <div class="form-group">
      <label data-i18n="cta">CTA</label>
      <select id="rpt-cta" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <option value="LEARN_MORE">Learn More</option>
        <option value="SIGN_UP">Sign Up</option>
        <option value="CONTACT_US">Contact Us</option>
        <option value="BOOK_NOW">Book Now</option>
        <option value="GET_OFFER">Get Offer</option>
        <option value="SUBSCRIBE">Subscribe</option>
      </select>
    </div>
    <div class="form-group">
      <label data-i18n="dest_url">Destination URL</label>
      <input type="url" id="rpt-link" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
    </div>
    <div class="form-group">
      <label data-i18n="media">Media (kept from original — upload new to replace)</label>
      <div class="upload-zone" onclick="document.getElementById('rpt-media-input').click()" style="border:2px dashed #ddd;border-radius:8px;padding:20px;text-align:center;cursor:pointer;">
        <div id="rpt-upload-placeholder" style="color:#65676b;">
          <div style="font-size:2rem;margin-bottom:8px;">+</div>
          <div data-i18n="click_to_upload">Click to upload images (multiple allowed)</div>
          <div style="font-size:.75rem;margin-top:4px;" data-i18n="media_hint">Select multiple images for carousel, or one video</div>
        </div>
        <div id="rpt-upload-preview" style="display:none;">
          <div id="rpt-carousel-thumbs" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;"></div>
          <video id="rpt-preview-video" style="max-height:120px;border-radius:6px;display:none;" controls></video>
          <div id="rpt-preview-filename" style="font-size:.85rem;margin-top:6px;font-weight:600;"></div>
        </div>
      </div>
      <input type="file" id="rpt-media-input" accept="image/*,video/*" style="display:none" multiple onchange="handleRptMediaUpload(this.files)">
      <div style="margin-top:6px;font-size:.8rem;color:#65676b;">
        <span><span data-i18n="images">Images</span>: <span id="rpt-img-count">0</span> | </span>
        <a href="#" onclick="event.preventDefault();document.getElementById('rpt-media-input').click();return false;" style="color:#1877f2;" data-i18n="add_more">Add more</a>
      </div>
      <div id="rpt-media-info" style="font-size:.85rem;margin-top:4px;color:#65676b;"></div>
    </div>

    <!-- Quick Schedule: date + hours + presets -->
    <div class="form-group" style="background:#f0f2f5;border-radius:8px;padding:12px;margin-bottom:12px;">
      <label style="font-weight:600;margin-bottom:8px;display:block;"><span data-i18n="quick_schedule">Quick Schedule</span></label>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <input type="date" id="rpt-start-date" style="flex:1;min-width:140px;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <select id="rpt-hour-select" style="padding:8px;border:1px solid #ddd;border-radius:6px;width:75px;">
          <option>12</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option><option>8</option><option>9</option><option>10</option><option>11</option>
        </select>
        <span style="font-weight:700;">:</span>
        <select id="rpt-minute-select" style="padding:8px;border:1px solid #ddd;border-radius:6px;width:75px;">
          <option>00</option><option>15</option><option>30</option><option>45</option>
        </select>
        <select id="rpt-ampm-select" style="padding:8px;border:1px solid #ddd;border-radius:6px;width:75px;">
          <option value="AM">AM</option><option value="PM">PM</option>
        </select>
        <button class="btn btn-sm btn-primary" onclick="addRepeatHour()" data-i18n="add_hour">+ Hour</button>
      </div>
      <div id="rpt-hours-list" style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;"></div>
      <div id="rpt-hours-empty" style="color:#65676b;font-size:.8rem;margin-top:4px;" data-i18n="no_hours">No hours added. Add hours and click a preset below.</div>
      <div style="margin-top:8px;display:flex;gap:4px;flex-wrap:wrap;">
        <button class="btn btn-sm" onclick="generateRepeatPreset(7)" style="background:#e7f3ff;" data-i18n="preset_7d">7 Days</button>
        <button class="btn btn-sm" onclick="generateRepeatPreset(28)" style="background:#e7f3ff;" data-i18n="preset_weekly">4 Weeks</button>
        <button class="btn btn-sm" onclick="generateRepeatPreset(90)" style="background:#e7f3ff;" data-i18n="preset_monthly">3 Months</button>
        <button class="btn btn-sm" onclick="generateRepeatPreset(180)" style="background:#e7f3ff;" data-i18n="preset_3m">6 Months</button>
        <button class="btn btn-sm" onclick="generateRepeatPreset(365)" style="background:#e7f3ff;" data-i18n="preset_12m">12 Months</button>
      </div>
    </div>

    <!-- Manual Schedule Times -->
    <div class="form-group">
      <label><span data-i18n="manual_times">Manual Times</span> <button class="btn btn-sm btn-primary" onclick="addRepeatTime()" style="margin-left:8px;" data-i18n="add_time">+ Add</button></label>
      <div id="rpt-times-list" style="margin-top:6px;"></div>
      <div id="rpt-times-empty" style="color:#65676b;font-size:.85rem;text-align:center;padding:12px;" data-i18n="no_times">No times added. Use Quick Schedule presets or add manually.</div>
    </div>
    <div class="form-group" style="text-align:right;">
      <button class="btn btn-success" onclick="scheduleRepeatCopies()" style="font-weight:700;" data-i18n="schedule_all">Schedule All</button>
    </div>
    <div id="rpt-status" style="margin-top:12px;font-size:.85rem;text-align:center;"></div>
  </div>
</div>

<!-- Campaign Detail Modal -->
<div class="modal-bg" id="detail-modal">
  <div class="modal" style="max-width:660px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 id="detail-title" data-i18n="details">Campaign Details</h3>
      <button onclick="closeDetail()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <div class="tabs">
      <div class="tab active" onclick="switchTab('performance')" data-i18n="performance">Performance</div>
      <div class="tab" onclick="switchTab('strategy')" data-i18n="strategy">Strategy</div>
      <div class="tab" onclick="switchTab('budget')" data-i18n="budget_allocation">Budget Allocation</div>
    </div>
    <div class="tab-content active" id="tab-performance">
      <div class="detail-grid" id="perf-metrics"></div>
      <div id="perf-opportunities"></div>
    </div>
    <div class="tab-content" id="tab-strategy">
      <div id="strategy-info"></div>
    </div>
    <div class="tab-content" id="tab-budget">
      <div id="budget-info"></div>
    </div>
    <div style="margin-top:20px;text-align:right;">
      <button class="btn btn-warn" id="detail-optimize-btn" data-i18n="optimize">Optimize</button>
      <button class="btn" onclick="closeDetail()" style="background:#e4e6eb;margin-left:8px;" data-i18n="close">Close</button>
    </div>
  </div>
</div>

<!-- Campaign Preview Modal -->
<div class="modal-bg" id="preview-modal">
  <div class="modal" style="max-width:560px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 id="preview-camp-name" style="margin:0;">Campaign</h3>
      <button onclick="closePreview()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div><strong style="color:#65676b;font-size:.8rem;">ID</strong><br><span id="preview-camp-id" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Status</strong><br><span id="preview-camp-status" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Objective</strong><br><span id="preview-camp-objective" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Ad Type</strong><br><span id="preview-camp-adtype" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Platforms</strong><br><span id="preview-camp-platforms" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Targeting</strong><br><span id="preview-camp-targeting" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Daily Budget</strong><br><span id="preview-camp-budget" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Lifetime Budget</strong><br><span id="preview-camp-lifetime" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Created</strong><br><span id="preview-camp-created" style="font-size:.85rem;">-</span></div>
      <div><strong style="color:#65676b;font-size:.8rem;">Strategy</strong><br><span id="preview-camp-strategy" style="font-size:.85rem;">-</span></div>
    </div>
    <div style="margin-top:20px;text-align:right;display:flex;gap:8px;justify-content:flex-end;">
      <button class="btn btn-warn" id="preview-optimize-btn" data-i18n="optimize">Optimize</button>
      <button class="btn btn-primary" id="preview-detail-btn" data-i18n="details">Details</button>
      <button class="btn" onclick="closePreview()" style="background:#e4e6eb;" data-i18n="close">Close</button>
    </div>
  </div>
</div>

<!-- Create Campaign Modal - 4 Step Wizard -->
<div class="modal-bg" id="create-modal">
  <div class="modal">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <h3 data-i18n="create_campaign_modal">Create New Campaign</h3>
      <button onclick="closeCreateModal()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>

    <!-- Step Indicator -->
    <div class="step-indicator">
      <div class="step active" data-step="1" data-i18n="step1_title">1. Business</div>
      <div class="step" data-step="2" data-i18n="step2_title">2. Ad Creative</div>
      <div class="step" data-step="3" data-i18n="step3_title">3. Targeting</div>
      <div class="step" data-step="4" data-i18n="step4_title">4. Preview</div>
    </div>

    <!-- Step 1: Business Info -->
    <div class="step-content" id="step-1">
      <div class="form-group">
        <label data-i18n="business_name">Business Name *</label>
        <input type="text" id="f-name" data-i18n-placeholder="biz_name_placeholder" placeholder="e.g. Joe's Pizza">
      </div>
      <div class="form-row">
        <div class="form-group">
          <label data-i18n="industry">Industry *</label>
          <select id="f-industry">
            <option value="service" data-i18n="industry_service">Service</option>
            <option value="restaurant" data-i18n="industry_restaurant">Restaurant</option>
            <option value="retail" data-i18n="industry_retail">Retail</option>
            <option value="ecommerce" data-i18n="industry_ecommerce">E-commerce</option>
            <option value="martialarts" data-i18n="industry_martialarts">Artes Marciales</option>
            <option value="medical" data-i18n="industry_medical">Salud / Medicina</option>
          </select>
        </div>
        <div class="form-group">
          <label data-i18n="biz_size">Business Size</label>
          <select id="f-size">
            <option value="small" data-i18n="size_small">Small</option>
            <option value="medium" data-i18n="size_medium">Medium</option>
            <option value="large" data-i18n="size_large">Large</option>
          </select>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label data-i18n="daily_budget">Daily Budget ($)</label>
          <input type="number" id="f-daily" value="50" min="1">
        </div>
        <div class="form-group">
          <label data-i18n="monthly_budget">Monthly Budget ($)</label>
          <input type="number" id="f-monthly" value="1500" min="1">
        </div>
      </div>
      <div class="form-group">
        <label data-i18n="website">Website</label>
        <input type="url" id="f-website" placeholder="https://yourbusiness.com">
      </div>
      <div class="form-group">
        <label data-i18n="language">Idioma / Language</label>
        <select id="f-language" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
          <option value="en" data-i18n="lang_en">English</option>
          <option value="es" selected data-i18n="lang_es">Español</option>
        </select>
      </div>
      <div style="margin-top:16px;text-align:right;">
        <button class="btn btn-primary" onclick="goStep(2)" data-i18n="next_ad_creative">Next: Ad Creative</button>
      </div>
    </div>

    <!-- Step 2: Ad Creative -->
    <div class="step-content" id="step-2" style="display:none;">
      <div class="form-group">
        <label data-i18n="headline">Headline *</label>
        <div style="display:flex;gap:6px;">
          <input type="text" id="f-headline" data-i18n-placeholder="headline_placeholder" placeholder="e.g. Special Offer!" maxlength="40" style="flex:1;">
          <button class="btn btn-sm btn-outline" onclick="generateAdCopy()" style="white-space:nowrap;flex-shrink:0;" data-i18n="gen_ai">Gen AI</button>
          <a href="https://chat.openai.com" target="_blank" class="btn btn-sm btn-outline" style="white-space:nowrap;flex-shrink:0;text-decoration:none;background:#10a37f;color:#fff;border-color:#10a37f;" data-i18n="chatgpt" title="Abrir ChatGPT">ChatGPT</a>
        </div>
      </div>
      <div class="form-group">
        <label data-i18n="primary_text">Primary Text *</label>
        <div style="display:flex;gap:6px;align-items:flex-start;">
          <textarea id="f-message" data-i18n-placeholder="primary_text_placeholder" placeholder="Describe your offer..." style="flex:1;min-height:60px;"></textarea>
          <button class="btn btn-sm btn-outline" onclick="generateAdCopy()" style="white-space:nowrap;flex-shrink:0;margin-top:0;" data-i18n="gen_ai">Gen AI</button>
        </div>
      </div>
      <div class="form-group">
        <label data-i18n="ai_instruction">Instrucci\u00f3n para la IA</label>
        <textarea id="f-ai-instruction" data-i18n-placeholder="ai_instruction_placeholder" placeholder="Ej: Quiero promocionar la inscripci\u00f3n de verano para ni\u00f1os, 50% de descuento, incluye uniforme gratis. Mencionar que son clases divertidas y seguras." style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;resize:vertical;min-height:60px;"></textarea>
        <div data-i18n="ai_instruction_hint" style="font-size:11px;color:#888;margin-top:2px">Describe what you want to promote. The AI will use this to generate unique headlines and text.</div>
      </div>
      <div class="form-group">
        <label data-i18n="cta">Call to Action</label>
        <select id="f-cta">
          <option value="LEARN_MORE" data-i18n="cta_learn_more">Learn More</option>
          <option value="SIGN_UP" data-i18n="cta_sign_up">Sign Up</option>
          <option value="CONTACT_US" data-i18n="cta_contact_us">Contact Us</option>
          <option value="BOOK_NOW" data-i18n="cta_book_now">Book Now</option>
          <option value="GET_OFFER" data-i18n="cta_get_offer">Get Offer</option>
          <option value="SUBSCRIBE" data-i18n="cta_subscribe">Subscribe</option>
        </select>
      </div>
      <div class="form-group">
        <label data-i18n="dest_url">Destination URL (where the button leads)</label>
        <input type="url" id="f-destination-url" placeholder="https://your-site.com/offer-page">
      </div>
      <div class="form-group">
        <label data-i18n="upload_media">Media (Images for Carousel or single Video)</label>
        <div class="upload-zone" id="upload-zone" onclick="document.getElementById('media-input').click()">
          <div id="upload-placeholder" style="color:#65676b;">
            <div style="font-size:2rem;margin-bottom:8px;">+</div>
            <div data-i18n="click_to_upload">Click to upload images (multiple allowed)</div>
            <div style="font-size:.75rem;margin-top:4px;" data-i18n="media_hint">Select multiple images for carousel, or one video</div>
          </div>
          <div id="upload-preview" style="display:none;">
            <div id="carousel-thumbs" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;"></div>
            <video id="preview-video" style="max-height:120px;border-radius:6px;display:none;" controls></video>
            <div id="preview-filename" style="font-size:.85rem;margin-top:6px;font-weight:600;"></div>
          </div>
        </div>
        <input type="file" id="media-input" accept="image/*,video/*" style="display:none" onchange="handleMediaUpload(event)" multiple>
        <input type="hidden" id="f-media-url" value="">
        <input type="hidden" id="f-media-urls" value="">
        <div style="margin-top:6px;font-size:.8rem;color:#65676b;">
          <span><span data-i18n="images">Imagenes</span>: <span id="img-count">0</span> | </span>
          <a href="#" onclick="event.preventDefault();document.getElementById('media-input').click();return false;" style="color:#1877f2;" data-i18n="add_more">Add more</a>
        </div>
      </div>
      <div style="margin-top:16px;display:flex;gap:10px;justify-content:space-between;">
        <button class="btn" onclick="goStep(1)" style="background:#e4e6eb;" data-i18n="back">Back</button>
        <button class="btn btn-primary" onclick="goStep(3)" data-i18n="next_targeting">Next: Targeting</button>
      </div>
    </div>

    <!-- Step 3: Targeting -->
    <div class="step-content" id="step-3" style="display:none;">
      <div class="form-group">
        <label data-i18n="location">Location</label>
        <input type="text" id="f-location" data-i18n-placeholder="location_placeholder" placeholder="Cooper City, Fl 33328 (o varios zips: 33328, 33024, 33330)">
      </div>
      <div class="form-group">
        <label data-i18n="radius">Targeting Radius</label>
        <div class="slider-container">
          <span style="font-size:.85rem;color:#65676b;">1 mi</span>
          <input type="range" id="f-radius" min="1" max="100" value="25" oninput="updateRadius()">
          <span style="font-size:.85rem;color:#65676b;">100 mi</span>
          <span class="slider-val" id="radius-val">25 mi</span>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label data-i18n="min_age">Min Age</label>
          <input type="number" id="f-age-min" value="18" min="13" max="65">
        </div>
        <div class="form-group">
          <label data-i18n="max_age">Max Age</label>
          <input type="number" id="f-age-max" value="65" min="13" max="65">
        </div>
      </div>
      <div class="form-group">
        <label data-i18n="platforms">Platforms</label>
        <select id="f-platforms" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;" onchange="toggleBudgetSplit()">
          <option value="both" data-i18n="both">Facebook + Instagram</option>
          <option value="facebook" data-i18n="facebook">Facebook Only</option>
          <option value="instagram" data-i18n="instagram">Instagram Only</option>
        </select>
      </div>
      <div class="form-group" id="budget-split-group" style="display:none;">
        <label data-i18n="budget_split">Budget Split</label>
        <div style="display:flex;align-items:center;gap:12px;">
          <span style="font-size:.85rem;">Facebook</span>
          <input type="range" id="f-fb-pct" min="0" max="100" value="60" oninput="updateBudgetSplit()" style="flex:1;">
          <span style="font-size:.85rem;">Instagram</span>
        </div>
        <div style="text-align:center;font-weight:700;color:#1877f2;margin-top:4px;">
          Facebook <span id="fb-pct-val">60</span>% / Instagram <span id="ig-pct-val">40</span>%
        </div>
      </div>
      <div class="form-group" id="placements-group">
        <label data-i18n="placements">Placements</label>
        <div style="display:flex;flex-wrap:wrap;gap:8px;padding:8px;border:1px solid #ddd;border-radius:6px;background:#f9f9f9;">
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="feed" checked onchange="updatePlacements()"> <span data-i18n="feed">Feed</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="story" checked onchange="updatePlacements()"> <span data-i18n="stories">Stories</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="marketplace" onchange="updatePlacements()"> <span data-i18n="marketplace">Marketplace</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;" class="ig-only"><input type="checkbox" value="reels" onchange="updatePlacements()"> <span data-i18n="reels">Reels</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;" class="ig-only"><input type="checkbox" value="explore" onchange="updatePlacements()"> <span data-i18n="explore">Explore</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="video_feeds" onchange="updatePlacements()"> <span data-i18n="video_feed">Video Feed</span></label>
        </div>
        <div style="font-size:11px;color:#888;margin-top:4px" data-i18n="placements_hint">Select where your ads will appear</div>
      </div>
      <div class="form-group">
        <label data-i18n="gender">Gender</label>
        <select id="f-gender" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
          <option value="all" data-i18n="all">All</option>
          <option value="male" data-i18n="male">Male</option>
          <option value="female" data-i18n="female">Female</option>
        </select>
      </div>
      <div class="form-group">
        <label data-i18n="optimization_goal">Optimization Goal</label>
        <select id="f-optimization-goal" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
          <option value="OUTCOME_LEADS" data-i18n="opt_leads">Leads</option>
          <option value="OUTCOME_TRAFFIC" data-i18n="opt_traffic">Traffic</option>
          <option value="OUTCOME_AWARENESS" data-i18n="opt_awareness">Awareness</option>
          <option value="LINK_CLICKS" data-i18n="opt_clicks">Link Clicks</option>
        </select>
        <div style="font-size:11px;color:#888;margin-top:4px" data-i18n="opt_goal_hint">How Meta optimizes delivery for your ad set</div>
      </div>
      <div class="form-group">
        <label data-i18n="interests">Interests</label>
        <div id="interest-presets" style="display:flex;flex-wrap:wrap;gap:6px;padding:8px;border:1px solid #ddd;border-radius:6px;background:#f9f9f9;margin-bottom:8px;"></div>
        <div style="display:flex;gap:6px;margin-bottom:6px;">
          <input type="text" id="f-custom-interest" data-i18n-placeholder="custom_interest_placeholder" placeholder="Nombre del inter\u00e9s o ID de Facebook" style="flex:1;padding:6px;border:1px solid #ddd;border-radius:6px;font-size:13px;">
          <button class="btn btn-sm btn-primary" onclick="addCustomInterest()" data-i18n="add_interest">Add</button>
        </div>
        <div id="interest-tags" style="display:flex;flex-wrap:wrap;gap:4px;"></div>
        <div style="font-size:11px;color:#888;margin-top:4px" data-i18n="interest_helper">Select predefined interests or add your own (Facebook numeric ID)</div>
      </div>
      <div class="form-group">
        <label data-i18n="has_children">Has Children</label>
        <select id="f-has-children" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
          <option value="" data-i18n="no_filter">No filter</option>
          <option value="yes" data-i18n="yes">Yes</option>
          <option value="no" data-i18n="no">No</option>
        </select>
      </div>
      <div style="margin-top:16px;display:flex;gap:10px;justify-content:space-between;">
        <button class="btn" onclick="goStep(2)" style="background:#e4e6eb;" data-i18n="back">Back</button>
        <button class="btn btn-primary" onclick="goStep(4)" data-i18n="next_preview">Next: Preview</button>
      </div>
    </div>

    <!-- Step 4: Preview & Submit -->
    <div class="step-content" id="step-4" style="display:none;">
      <div style="text-align:center;margin-bottom:12px;font-size:.85rem;color:#65676b;">
        <strong data-i18n="ad_preview">Ad Preview</strong> &mdash; <span data-i18n="ad_preview_desc">This is how your ad will appear</span>
      </div>
      <div class="preview-card">
        <div class="preview-header">
          <div class="avatar" id="preview-avatar">MB</div>
          <div>
            <div class="page-name" id="preview-page-name">Your Business</div>
            <div class="sponsored" data-i18n="sponsored">Sponsored</div>
          </div>
        </div>
        <div id="preview-media-area" style="background:#e4e6eb;height:200px;display:flex;align-items:center;justify-content:center;color:#65676b;font-size:.85rem;overflow:hidden;">
          <span data-i18n="no_media">No media selected</span>
        </div>
        <div class="preview-body">
          <div class="preview-title" id="preview-headline">Your Headline Here</div>
          <div class="preview-desc" id="preview-message">Your ad message will appear here.</div>
        </div>
        <a class="preview-cta" id="preview-cta" href="#" target="_blank">Learn More</a>
        <div class="preview-target" id="preview-target">Location: Miami, FL &middot; 25 mi radius &middot; Ages 18-65</div>
      </div>

      <div id="create-alert"></div>
      <div class="alert alert-warn" style="background:#fff3cd;border:1px solid #ffc107;color:#856404;padding:12px;border-radius:6px;margin-bottom:12px;display:flex;align-items:flex-start;gap:8px;">
        <span style="font-size:1.2rem;">⚠</span>
        <div>
          <strong data-i18n="safety_header">IMPORTANT:</strong> <span data-i18n="safety_body">When creating the campaign, Meta creates it in <strong>PAUSED</strong> state. If you already have a card configured, check <strong>immediately</strong> in Meta Ads Manager that Campaign, Ad Set, and Ad are <strong>OFF</strong> (gray). Meta may reactivate them when you add a card.</span>
        </div>
      </div>
      <div style="margin-bottom:12px;">
        <a id="ads-manager-link" href="https://business.facebook.com/adsmanager/manage/campaigns" target="_blank" 
           class="btn btn-outline" style="background:#1877f2;color:white;" data-i18n="open_ads_manager">Abrir Meta Ads Manager</a>
      </div>
      <label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer;font-size:.9rem;color:#1c1e21;">
        <input type="checkbox" id="safety-confirm" style="margin-top:2px;transform:scale(1.2);">
        <span><strong data-i18n="safety_confirm">Confirmo que revisaré el estado OFF/ON en Meta Ads Manager inmediatamente tras crear la campaña</strong></span>
      </label>
      <div id="create-alert"></div>
      <div style="margin-top:16px;display:flex;gap:10px;justify-content:space-between;">
        <button class="btn" onclick="goStep(3)" style="background:#e4e6eb;" data-i18n="back">Back</button>
        <button class="btn btn-success" onclick="submitCampaign()" id="submit-btn" disabled data-i18n="submit">Create Campaign</button>
      </div>
    </div>

  </div>
</div>

<div id="toast"></div>

<script>
var lang = (function() { try { return localStorage.getItem('meta_ads_lang') || 'en'; } catch(e) { return 'en'; } })();
var langData = {
  en: {
    title: 'Meta Ads Agent Dashboard',
    campaigns_tab: 'Campaigns',
    content_tab: 'Content',
    reports_tab: 'Reports',
    settings_tab: 'Settings',
    total_campaigns: 'Total Campaigns',
    active: 'Active',
    total_budget: 'Total Budget',
    total_leads: 'Total Leads',
    create_campaign: '+ Create Campaign',
    optimize_all: 'Optimize All',
    refresh: 'Refresh',
    campaign_name: 'Campaign Name',
    ad_type: 'Ad Type',
    targeting: 'Targeting',
    budget: 'Budget',
    status: 'Status',
    created: 'Created',
    actions: 'Actions',
    details: 'Details',
    optimize: 'Optimize',
    reuse: 'Re-use',
    delete: 'Delete',
    no_campaigns: 'No campaigns yet.',
    step1_title: 'Business Info',
    step2_title: 'Ad Creative',
    step3_title: 'Targeting',
    step4_title: 'Preview',
    business_name: 'Business Name',
    industry: 'Industry',
    biz_size: 'Business Size',
    daily_budget: 'Daily Budget ($)',
    monthly_budget: 'Monthly Budget ($)',
    website: 'Website',
    language: 'Language',
    next: 'Next',
    back: 'Back',
    submit: 'Submit Campaign',
    headline: 'Headline',
    primary_text: 'Primary Text',
    ai_instruction: 'AI Instruction (what to promote?)',
    gen_ai: 'Gen AI',
    chatgpt: 'ChatGPT',
    cta: 'Call to Action',
    dest_url: 'Destination URL',
    upload_media: 'Upload Media',
    location: 'Location (city, zip)',
    radius: 'Radius (miles)',
    age_range: 'Age Range',
    platforms: 'Platforms',
    both: 'Both',
    facebook: 'Facebook',
    instagram: 'Instagram',
    placements: 'Placements',
    feed: 'Feed',
    stories: 'Stories',
    marketplace: 'Marketplace',
    reels: 'Reels',
    explore: 'Explore',
    video_feed: 'Video Feed',
    gender: 'Gender',
    all: 'All',
    male: 'Male',
    female: 'Female',
    interests: 'Interests',
    has_children: 'Has Children',
    no_filter: 'No filter',
    yes: 'Yes',
    no: 'No',
    add_interest: 'Add',
    custom_interest_placeholder: 'Interest name or Facebook ID',
    no_interests: 'No interests selected',
    ad_preview: 'Ad Preview',
    sponsored: 'Sponsored',
    your_headline: 'Your Headline Here',
    your_message: 'Your ad message will appear here.',
    safety_confirm: 'I understand the campaign will be created in OFF (PAUSED) state. I will verify and activate it in Meta Ads Manager.',
    open_ads_manager: 'Open Meta Ads Manager',
    all_posts: 'All Posts',
    todos: 'Todos',
    published: 'Published',
    drafts: 'Drafts',
    scheduled: 'Scheduled',
    trash: 'Trash',
    all_platforms: 'All platforms',
    search_posts: 'Search posts...',
    no_posts: 'No posts yet.',
    no_matches: 'No posts match your filters.',
    type: 'Type',
    platform: 'Platform',
    message: 'Message',
    schedule: 'Schedule',
    publish: 'Publish',
    publish_now: 'Publish Now',
    view: 'View',
    restore: 'Restore',
    delete_forever: 'Delete Forever',
    prev: 'Prev',
    next: 'Next',
    page: 'Page',
    of: 'of',
    posts: 'posts',
    filtered_from: 'filtered from',
    total_spend: 'Total Spend',
    cost_per_lead: 'Cost/Lead',
    ctr: 'CTR',
    cpc: 'CPC',
    campaign_performance: 'Campaign Performance',
    spend: 'Spend',
    clicks: 'Clicks',
    impressions: 'Impressions',
    leads: 'Leads',
    ad_accounts: 'Ad Accounts',
    add_account: 'Add Account',
    account_name: 'Account Name',
    access_token: 'Access Token',
    ad_account_id: 'Ad Account ID',
    app_id: 'App ID (optional)',
    app_secret: 'App Secret (optional)',
    save: 'Save',
    delete_account: 'Delete',
    meta_pixel: 'Meta Pixel',
    pixel_id: 'Pixel ID',
    save_pixel: 'Save Pixel',
    calendar: 'Calendar',
    prev_month: 'Prev',
    next_month: 'Next',
    create_post: 'Create Post',
    save_draft: 'Save as Draft',
    schedule_post: 'Schedule',
    publish_post: 'Publish',
    campaign_created: 'Campaign created! Check Meta Ads Manager to activate.',
    campaign_deleted: 'Campaign archived.',
    campaign_loaded: 'Campaign loaded - adjust and create',
    optimizing: 'Optimizing all campaigns...',
    optimized: 'All campaigns optimized!',
    post_published: 'Published!',
    post_restored: 'Post restored',
    post_deleted_permanent: 'Post permanently deleted',
    moved_to_trash: 'Moved to trash',
    error: 'Error',
    confirm_trash: 'Move this post to trash?',
    confirm_delete_forever: 'Delete this post permanently? Cannot be undone.',
    safety_alert: 'You must confirm you will check the OFF/ON status in Meta Ads Manager after creating the campaign.',
    select_form: 'Select a form...',
    load_leads: 'Load Leads',
    download_csv: 'Download CSV',
    refresh_forms: 'Refresh Forms',
    no_lead_forms: 'No lead forms found.',
    no_leads: 'No leads yet.',
    manage_accounts: 'Manage multiple ad accounts for different clients.',
    pixel_desc: 'Optional: Add your Meta Pixel ID for conversion tracking.',
    create_campaign_modal: 'Create New Campaign',
    biz_name_placeholder: "e.g. Joe's Pizza",
    headline_placeholder: 'e.g. Special Offer!',
    primary_text_placeholder: 'Describe your offer...',
    write_post: 'Write your post...',
    media_hint: 'Select multiple images for carousel, or one video',
    schedule_label: 'Schedule (optional \u2014 leave empty for draft)',
    performance: 'Performance',
    strategy: 'Strategy',
    budget_allocation: 'Budget Allocation',
    close: 'Close',
    next_ad_creative: 'Next: Ad Creative',
    next_targeting: 'Next: Targeting',
    next_preview: 'Next: Preview',
    min_age: 'Min Age',
    max_age: 'Max Age',
    ad_preview_desc: 'This is how your ad will appear',
    selected_location: 'Selected Location',
    location_label: 'Location:',
    mi_radius: 'mi radius',
    ages: 'Ages',
    location_placeholder: 'Cooper City, Fl 33328 (or multiple zips: 33328, 33024, 33330)',
    all_platforms_value: 'All platforms',
    create_one: 'Create one in Ads Manager',
    loading: 'Loading...',
    no_campaigns_reports: 'No campaigns',
    confirm_delete_account: 'Delete this account?',
    confirm_delete_campaign: 'Delete this campaign? It will be moved to trash.',
    confirm_delete_forever: 'Delete this campaign permanently? This cannot be undone.',
    campaign_restored: 'Campaign restored!',
    no_trashed_campaigns: 'No trashed campaigns.',
    no_optimizations: 'No optimizations available.',
    optimize_confirm: 'The following changes will be applied to this campaign in Meta:',
    opt_adjust_budget: 'Adjust budget allocation based on performance',
    opt_test_audiences: 'Test new audiences - suggestion only',
    opt_refresh_creative: 'Refresh ad creative - suggestion only',
    opt_optimize_targeting: 'Optimize targeting - suggestion only',
    opt_improve_copy: 'Improve ad copy - suggestion only',
    opt_scale: 'Scale successful elements (increase budget)',
    opt_increase_budget: 'Increase budget by 20%',
    opt_expand_targeting: 'Expand targeting - suggestion only',
    opt_duplicate: 'Duplicate creative - suggestion only',
    confirm_publish_post: 'Publish this post now?',
    select_form_first: 'Select a lead form',
    ad_copy_generated: 'Ad copy generated!',
    generation_failed: 'Generation failed',
    campaign_optimized: 'Campaign optimized!',
    enter_biz_name: 'Enter a business name first',
    campaign_not_found: 'Campaign not found',
    no_additional_accounts: 'No additional accounts. Using default from .env.txt',
    select_schedule: 'Please select a date/time to schedule.',
    opportunities: 'OPPORTUNITIES',
    no_issues: 'No issues found',
    objectives: 'Objectives',
    ad_types: 'Ad Types',
    bidding: 'Bidding',
    january: 'January',
    february: 'February',
    march: 'March',
    april: 'April',
    may: 'May',
    june: 'June',
    july: 'July',
    august: 'August',
    september: 'September',
    october: 'October',
    november: 'November',
    december: 'December',
    sun: 'Sun',
    mon: 'Mon',
    tue: 'Tue',
    wed: 'Wed',
    thu: 'Thu',
    fri: 'Fri',
    sat: 'Sat',
    image: 'Image',
    video: 'Video',
    carousel: 'Carousel',
    no_message: 'no message',
    account_required: 'Name, token, and ad account are required',
    account_saved: 'Account saved!',
    account_deleted: 'Account deleted',
    pixel_saved: 'Pixel saved!',
    error_saving_pixel: 'Error saving pixel',
    biz_name_required: 'Business name is required.',
    creating: 'Creating...',
    error_creating: 'Error creating campaign',
    leads_downloaded: 'Leads downloaded!',
    error_loading_forms: 'Error loading forms',
    error_loading_leads: 'Error loading leads',
    error_loading_details: 'Error loading details',
    images_uploaded: 'images uploaded',
    selected: 'selected',
    repeat: 'Repeat',
    repeat_schedule: 'Repeat Schedule',
    add_time: 'Add Time',
    remove_time: 'Remove',
    schedule_all: 'Schedule All',
    no_times: 'No times added',
    uploaded: 'Uploaded',
    upload_failed: 'Upload failed',
    upload_error_at: 'Upload error at',
    uploading_video: 'Uploading video...',
    video_uploaded: 'Video uploaded!',
    upload_error: 'Upload error',
    uploading: 'Uploading...',
    image_uploaded: 'Image uploaded!',
    processing: 'Processing...',
    date: 'Date',
    draft_saved: 'Draft saved!',
    post_scheduled: 'Post scheduled!',
    publishing: 'Publishing...',
    interest_already_added: 'Interest already added',
    interest_helper: 'Select predefined interests or add your own (Facebook numeric ID)',
    ai_instruction_placeholder: 'E.g. I want to promote summer enrollment for kids, 50% off, includes free uniform. Mention fun and safe classes.',
    int_local_business: 'Local Business',
    int_health: 'Health',
    int_fitness: 'Fitness',
    int_sports: 'Sports',
    int_tech: 'Technology',
    int_cooking: 'Cooking',
    int_travel: 'Travel',
    int_fashion: 'Fashion',
    int_online_shopping: 'Online Shopping',
    int_martial_arts: 'Martial Arts',
    int_jiu_jitsu: 'Jiu-Jitsu',
    int_kickboxing: 'Kickboxing',
    int_mma: 'MMA',
    int_self_defense: 'Self Defense',
    budget_split: 'Budget Split',
    click_to_upload: 'Click to upload images (multiple allowed)',
    no_media: 'No media selected',
    add_more: 'Add more',
    images: 'Images',
    images_selected: 'images selected',
    industry_service: 'Service',
    industry_restaurant: 'Restaurant',
    industry_retail: 'Retail',
    industry_ecommerce: 'E-commerce',
    industry_martialarts: 'Martial Arts',
    industry_medical: 'Health / Medicine',
    size_small: 'Small',
    size_medium: 'Medium',
    size_large: 'Large',
    cta_learn_more: 'Learn More',
    cta_sign_up: 'Sign Up',
    cta_contact_us: 'Contact Us',
    cta_book_now: 'Book Now',
    cta_get_offer: 'Get Offer',
    cta_subscribe: 'Subscribe',
    lang_en: 'English',
    lang_es: 'Espa\u00f1ol',
    leads_tab: 'Leads',
    multiplatform_tab: 'Multi-Platform',
    responder_tab: 'Auto Responder',
    lead_management: 'Lead Management',
    fetch_leads: 'Fetch from Meta',
    add_lead: '+ Add Lead',
    new_leads: 'New',
    contacted: 'Contacted',
    converted: 'Converted',
    lost: 'Lost',
    avg_score: 'Avg Score',
    search_leads: 'Search leads...',
    score: 'Score',
    source: 'Source',
    name: 'Name',
    email: 'Email',
    phone: 'Phone',
    notes: 'Notes',
    save_lead: 'Save Lead',
    workflows: 'Workflows',
    create_workflow: '+ Create Workflow',
    workflow_name: 'Workflow Name',
    workflow_steps: 'Steps (JSON array)',
    save_workflow: 'Save Workflow',
    no_scheduled_posts: 'No scheduled posts.',
    enabled: 'Enabled',
    scan_comments: 'Scan & Respond',
    auto_responder: 'Social Media Auto Responder',
    add_rule: '+ Add Rule',
    use_ai: 'Use AI Responses',
    default_response: 'Default:',
    auto_reply_rules: 'Auto-Reply Rules',
    response_log: 'Response Log',
    from: 'From',
    comment: 'Comment',
    response: 'Response',
    no_responses: 'No responses yet.',
    keyword: 'Keyword',
    save_rule: 'Save Rule',
    multi_platform_scheduler: 'Multi-Platform Scheduler',
    schedule_post: 'Schedule Post',
    schedule: 'Schedule',
    save_schedule: 'Save Schedule',
    fetching_leads: 'Fetching leads from Meta...',
    lead_deleted: 'Lead deleted!',
    name_required: 'Name is required',
    lead_added: 'Lead added!',
    invalid_json_steps: 'Invalid JSON for steps',
    workflow_created: 'Workflow created!',
    workflow_deleted: 'Workflow deleted!',
    select_platform: 'Select at least one platform',
    deleted: 'Deleted!',
    default_response_saved: 'Default response saved!',
    keyword_response_required: 'Keyword and response are required',
    rule_added: 'Rule added!',
    rule_deleted: 'Rule deleted!',
    scanning_comments: 'Scanning comments...',
    error_scanning: 'Error scanning',
    quick_schedule: 'Quick Schedule',
    single_post_hint: 'Set one date & time for THIS post.',
    repeat_hint: "Want to repeat this same post automatically (so you don't have to schedule it day by day)? Save it first, then choose how often:",
    time_added: 'Time added',
    add_content_first: 'Add a message or image first.',
    repeat_setup_hint: 'Draft saved. Add at least one time of day below, then this preset will fill in the dates automatically.',
    no_time_yet: 'No time added yet. Pick date & time and click "+ Add".',
    add_hour: '+ Hour',
    no_hours: 'No hours added.',
    add_hours_first: 'Add hours first.',
    select_start_date: 'Select a start date.',
    select_hour_first: 'Select an hour first.',
    preset_7d: '7 Days',
    preset_weekly: '4 Weeks',
    preset_monthly: '3 Months',
    preset_3m: '6 Months',
    preset_12m: '12 Months',
    manual_times: 'Manual Times',
    slots_generated: 'slots generated!',
    login_title: 'Sign In',
    login_desc: 'This dashboard has multiple clients configured. Enter your credentials.',
    login_client_id: 'Client (tenant ID)',
    sign_in: 'Sign In',
    logout: 'logout',
    safety_header: 'IMPORTANT:',
    safety_body: 'When creating the campaign, Meta creates it in PAUSED state. If you already have a card configured, check immediately in Meta Ads Manager that Campaign, Ad Set, and Ad are OFF (gray). Meta may reactivate them when you add a card.',
    kpi_title: 'Is it working?',
    kpi_refresh: 'refresh',
    kpi_cost_per_lead: 'cost/lead',
    kpi_booked: 'booked',
    kpi_enrolled: 'enrolled',
    remove: 'Remove',
    optimization_goal: 'Optimization Goal',
    opt_leads: 'Leads',
    opt_traffic: 'Traffic',
    opt_awareness: 'Awareness',
    opt_clicks: 'Link Clicks',
    opt_goal_hint: 'How Meta optimizes delivery for your ad set',
    placements_hint: 'Select where your ads will appear',
    ai_instruction_hint: 'Describe what you want to promote. The AI will use this to generate unique headlines and text.',
    no_interests_selected: 'No interests selected',
    default_business_name: 'Your Business',
    placements_label: 'Placements:',
    token_unlimited: 'Token: Unlimited',
    token_expiring: 'Token expires in {days} days - RENEW',
    token_renew_soon: 'Token: {days} days - Renew soon',
    token_days: 'Token: {days} days',
    confidence_sin_datos: 'No leads recorded for this period yet.',
    confidence_muy_bajo: 'Fewer than 10 leads \u2014 just an initial sample, don\\'t draw conclusions yet.',
    confidence_bajo: 'Between 10 and 30 leads \u2014 starting to be useful, but wait for more before making big changes.',
    confidence_aceptable: '30+ leads \u2014 enough to start trusting these numbers.',
  },
  es: {
    title: 'Panel de Control de Anuncios',
    campaigns_tab: 'Campa\u00f1as',
    content_tab: 'Contenido',
    reports_tab: 'Reportes',
    settings_tab: 'Configuraci\u00f3n',
    total_campaigns: 'Total Campa\u00f1as',
    active: 'Activas',
    total_budget: 'Presupuesto Total',
    total_leads: 'Total Leads',
    create_campaign: '+ Crear Campa\u00f1a',
    optimize_all: 'Optimizar Todo',
    refresh: 'Actualizar',
    campaign_name: 'Nombre de Campa\u00f1a',
    ad_type: 'Tipo de Anuncio',
    targeting: 'Segmentaci\u00f3n',
    budget: 'Presupuesto',
    status: 'Estado',
    created: 'Creado',
    actions: 'Acciones',
    details: 'Detalles',
    optimize: 'Optimizar',
    reuse: 'Re-usar',
    delete: 'Eliminar',
    no_campaigns: 'A\u00fan no hay campa\u00f1as.',
    step1_title: 'Informaci\u00f3n del Negocio',
    step2_title: 'Creativo del Anuncio',
    step3_title: 'Segmentaci\u00f3n',
    step4_title: 'Vista Previa',
    business_name: 'Nombre del Negocio',
    industry: 'Industria',
    biz_size: 'Tama\u00f1o del Negocio',
    daily_budget: 'Presupuesto Diario ($)',
    monthly_budget: 'Presupuesto Mensual ($)',
    website: 'Sitio Web',
    language: 'Idioma',
    next: 'Siguiente',
    back: 'Atr\u00e1s',
    submit: 'Crear Campa\u00f1a',
    headline: 'Titular',
    primary_text: 'Texto Principal',
    ai_instruction: 'Instrucci\u00f3n AI (\u00bfqu\u00e9 promocionar?)',
    gen_ai: 'Gen AI',
    chatgpt: 'ChatGPT',
    cta: 'Llamado a la Acci\u00f3n',
    dest_url: 'URL de Destino',
    upload_media: 'Subir Media',
    location: 'Ubicaci\u00f3n (ciudad, c\u00f3digo postal)',
    radius: 'Radio (millas)',
    age_range: 'Rango de Edad',
    platforms: 'Plataformas',
    both: 'Ambas',
    facebook: 'Facebook',
    instagram: 'Instagram',
    placements: 'Ubicaciones',
    feed: 'Feed',
    stories: 'Historias',
    marketplace: 'Marketplace',
    reels: 'Reels',
    explore: 'Explorar',
    video_feed: 'Video Feed',
    gender: 'G\u00e9nero',
    all: 'Todos',
    male: 'Hombres',
    female: 'Mujeres',
    interests: 'Intereses',
    has_children: 'Tiene Hijos',
    no_filter: 'No filtrar',
    yes: 'S\u00ed',
    no: 'No',
    add_interest: 'Agregar',
    custom_interest_placeholder: 'Nombre del inter\u00e9s o ID de Facebook',
    no_interests: 'Ning\u00fan inter\u00e9s seleccionado',
    ad_preview: 'Vista Previa del Anuncio',
    sponsored: 'Patrocinado',
    your_headline: 'Tu Titular Aqu\u00ed',
    your_message: 'El mensaje de tu anuncio aparecer\u00e1 aqu\u00ed.',
    safety_confirm: 'Entiendo que la campa\u00f1a se crear\u00e1 en estado OFF (PAUSADA). Verificar\u00e9 y la activar\u00e9 en Meta Ads Manager.',
    open_ads_manager: 'Abrir Meta Ads Manager',
    all_posts: 'Todos los Posts',
    todos: 'Todos',
    published: 'Publicados',
    drafts: 'Borradores',
    scheduled: 'Programados',
    trash: 'Papelera',
    all_platforms: 'Todas las plataformas',
    search_posts: 'Buscar posts...',
    no_posts: 'A\u00fan no hay posts.',
    no_matches: 'Ning\u00fan post coincide con los filtros.',
    type: 'Tipo',
    platform: 'Plataforma',
    message: 'Mensaje',
    schedule: 'Programado',
    publish: 'Publicar',
    publish_now: 'Publicar Ahora',
    view: 'Ver',
    restore: 'Restaurar',
    delete_forever: 'Eliminar para siempre',
    prev: 'Anterior',
    next: 'Siguiente',
    page: 'P\u00e1gina',
    of: 'de',
    posts: 'posts',
    filtered_from: 'filtrados de',
    total_spend: 'Gasto Total',
    cost_per_lead: 'Costo/Lead',
    ctr: 'CTR',
    cpc: 'CPC',
    campaign_performance: 'Rendimiento de Campa\u00f1as',
    spend: 'Gasto',
    clicks: 'Clicks',
    impressions: 'Impresiones',
    leads: 'Leads',
    ad_accounts: 'Cuentas Publicitarias',
    add_account: 'Agregar Cuenta',
    account_name: 'Nombre de Cuenta',
    access_token: 'Token de Acceso',
    ad_account_id: 'ID de Cuenta Publicitaria',
    app_id: 'App ID (opcional)',
    app_secret: 'App Secret (opcional)',
    save: 'Guardar',
    delete_account: 'Eliminar',
    meta_pixel: 'Meta Pixel',
    pixel_id: 'ID del Pixel',
    save_pixel: 'Guardar Pixel',
    calendar: 'Calendario',
    prev_month: 'Anterior',
    next_month: 'Siguiente',
    create_post: 'Crear Post',
    save_draft: 'Guardar Borrador',
    schedule_post: 'Programar',
    publish_post: 'Publicar',
    campaign_created: 'Campa\u00f1a creada! Revisa Meta Ads Manager para activarla.',
    campaign_deleted: 'Campa\u00f1a archivada.',
    campaign_loaded: 'Campa\u00f1a cargada - ajusta y crea',
    optimizing: 'Optimizando campa\u00f1as...',
    optimized: 'Campa\u00f1as optimizadas!',
    post_published: 'Publicado!',
    post_restored: 'Post restaurado',
    post_deleted_permanent: 'Post eliminado permanentemente',
    moved_to_trash: 'Movido a la papelera',
    error: 'Error',
    confirm_trash: 'Mover este post a la papelera?',
    confirm_delete_forever: 'Eliminar este post permanentemente? No se puede deshacer.',
    safety_alert: 'Debes confirmar que revisar\u00e1s el estado OFF/ON en Meta Ads Manager tras crear la campa\u00f1a.',
    select_form: 'Seleccionar un formulario...',
    load_leads: 'Cargar Leads',
    download_csv: 'Descargar CSV',
    refresh_forms: 'Actualizar Formularios',
    no_lead_forms: 'No se encontraron formularios de leads.',
    no_leads: 'A\u00fan no hay leads.',
    manage_accounts: 'Administra m\u00faltiples cuentas publicitarias para diferentes clientes.',
    pixel_desc: 'Opcional: Agrega tu ID de Meta Pixel para seguimiento de conversiones.',
    create_campaign_modal: 'Crear Nueva Campa\u00f1a',
    biz_name_placeholder: 'Ej: La Pizzer\u00eda de Juan',
    headline_placeholder: 'Ej: Oferta Especial!',
    primary_text_placeholder: 'Describe tu oferta...',
    write_post: 'Escribe tu post...',
    media_hint: 'Selecciona m\u00faltiples im\u00e1genes para carrusel, o un video',
    schedule_label: 'Programar (opcional \u2014 deja vac\u00edo para borrador)',
    performance: 'Rendimiento',
    strategy: 'Estrategia',
    budget_allocation: 'Asignaci\u00f3n de Presupuesto',
    close: 'Cerrar',
    next_ad_creative: 'Siguiente: Creativo',
    next_targeting: 'Siguiente: Segmentaci\u00f3n',
    next_preview: 'Siguiente: Vista Previa',
    min_age: 'Edad M\u00ednima',
    max_age: 'Edad M\u00e1xima',
    ad_preview_desc: 'As\u00ed se ver\u00e1 tu anuncio',
    selected_location: 'Ubicaci\u00f3n Seleccionada',
    location_label: 'Ubicaci\u00f3n:',
    mi_radius: 'millas de radio',
    ages: 'Edades',
    location_placeholder: 'Cooper City, FL 33328 (o varios c\u00f3digos: 33328, 33024, 33330)',
    all_platforms_value: 'Todas las plataformas',
    create_one: 'Crear uno en Ads Manager',
    loading: 'Cargando...',
    no_campaigns_reports: 'Sin campa\u00f1as',
    confirm_delete_account: 'Eliminar esta cuenta?',
    confirm_delete_campaign: 'Eliminar esta campa\u00f1a? Ir\u00e1 a la papelera.',
    confirm_delete_forever: 'Eliminar esta campa\u00f1a permanentemente? Esto no se puede deshacer.',
    campaign_restored: 'Campa\u00f1a restaurada!',
    no_trashed_campaigns: 'No hay campa\u00f1as en la papelera.',
    no_optimizations: 'No hay optimizaciones disponibles.',
    optimize_confirm: 'Se aplicar\u00e1n estos cambios a la campa\u00f1a en Meta:',
    opt_adjust_budget: 'Ajustar presupuesto seg\u00fan rendimiento',
    opt_test_audiences: 'Probar nuevas audiencias - solo sugerencia',
    opt_refresh_creative: 'Renovar creatividad - solo sugerencia',
    opt_optimize_targeting: 'Optimizar segmentaci\u00f3n - solo sugerencia',
    opt_improve_copy: 'Mejorar texto del anuncio - solo sugerencia',
    opt_scale: 'Escalar elementos exitosos (subir presupuesto)',
    opt_increase_budget: 'Aumentar presupuesto 20%',
    opt_expand_targeting: 'Expandir segmentaci\u00f3n - solo sugerencia',
    opt_duplicate: 'Duplicar creatividad - solo sugerencia',
    confirm_publish_post: 'Publicar este post ahora?',
    select_form_first: 'Selecciona un formulario de leads',
    ad_copy_generated: 'Texto publicitario generado!',
    generation_failed: 'Generaci\u00f3n fallida',
    campaign_optimized: 'Campa\u00f1a optimizada!',
    enter_biz_name: 'Ingresa el nombre del negocio primero',
    campaign_not_found: 'Campa\u00f1a no encontrada',
    no_additional_accounts: 'Sin cuentas adicionales. Usando la predeterminada de .env.txt',
    select_schedule: 'Selecciona una fecha/hora para programar.',
    opportunities: 'OPORTUNIDADES',
    no_issues: 'Sin problemas encontrados',
    objectives: 'Objetivos',
    ad_types: 'Tipos de Anuncio',
    bidding: 'Puja',
    january: 'Enero',
    february: 'Febrero',
    march: 'Marzo',
    april: 'Abril',
    may: 'Mayo',
    june: 'Junio',
    july: 'Julio',
    august: 'Agosto',
    september: 'Septiembre',
    october: 'Octubre',
    november: 'Noviembre',
    december: 'Diciembre',
    sun: 'Dom',
    mon: 'Lun',
    tue: 'Mar',
    wed: 'Mi\u00e9',
    thu: 'Jue',
    fri: 'Vie',
    sat: 'S\u00e1b',
    image: 'Imagen',
    video: 'Video',
    carousel: 'Carrusel',
    no_message: 'sin mensaje',
    account_required: 'Nombre, token y cuenta publicitaria son requeridos',
    account_saved: 'Cuenta guardada!',
    account_deleted: 'Cuenta eliminada',
    pixel_saved: 'Pixel guardado!',
    error_saving_pixel: 'Error al guardar pixel',
    biz_name_required: 'El nombre del negocio es requerido.',
    creating: 'Creando...',
    error_creating: 'Error al crear campa\u00f1a',
    leads_downloaded: 'Leads descargados!',
    error_loading_forms: 'Error al cargar formularios',
    error_loading_leads: 'Error al cargar leads',
    error_loading_details: 'Error al cargar detalles',
    images_uploaded: 'im\u00e1genes subidas',
    selected: 'seleccionado',
    repeat: 'Repetir',
    repeat_schedule: 'Programar Repetici\u00f3n',
    add_time: 'Agregar Hora',
    remove_time: 'Quitar',
    schedule_all: 'Programar Todo',
    no_times: 'Sin horas agregadas',
    uploaded: 'Subido',
    upload_failed: 'Subida fallida',
    upload_error_at: 'Error de subida en',
    uploading_video: 'Subiendo video...',
    video_uploaded: 'Video subido!',
    upload_error: 'Error de subida',
    uploading: 'Subiendo...',
    image_uploaded: 'Imagen subida!',
    processing: 'Procesando...',
    date: 'Fecha',
    draft_saved: 'Borrador guardado!',
    post_scheduled: 'Post programado!',
    publishing: 'Publicando...',
    interest_already_added: 'Inter\u00e9s ya agregado',
    interest_helper: 'Selecciona intereses predefinidos o agrega los tuyos (ID num\u00e9rico de Facebook Ads)',
    ai_instruction_placeholder: 'Ej: Quiero promocionar la inscripci\u00f3n de verano para ni\u00f1os, 50% de descuento, incluye uniforme gratis. Mencionar que son clases divertidas y seguras.',
    int_local_business: 'Negocios locales',
    int_health: 'Salud',
    int_fitness: 'Fitness',
    int_sports: 'Deportes',
    int_tech: 'Tecnolog\u00eda',
    int_cooking: 'Cocina',
    int_travel: 'Viajes',
    int_fashion: 'Moda',
    int_online_shopping: 'Compras en l\u00ednea',
    int_martial_arts: 'Artes marciales',
    int_jiu_jitsu: 'Jiu-Jitsu',
    int_kickboxing: 'Kickboxing',
    int_mma: 'MMA',
    int_self_defense: 'Defensa personal',
    budget_split: 'Reparto del Presupuesto',
    click_to_upload: 'Haz clic para subir im\u00e1genes (m\u00faltiples permitidas)',
    no_media: 'Sin media seleccionada',
    add_more: 'Agregar m\u00e1s',
    images: 'Im\u00e1genes',
    images_selected: 'im\u00e1genes seleccionadas',
    industry_service: 'Servicio',
    industry_restaurant: 'Restaurante',
    industry_retail: 'Venta al por menor',
    industry_ecommerce: 'Comercio electr\u00f3nico',
    industry_martialarts: 'Artes Marciales',
    industry_medical: 'Salud / Medicina',
    size_small: 'Peque\u00f1o',
    size_medium: 'Mediano',
    size_large: 'Grande',
    cta_learn_more: 'M\u00e1s Informaci\u00f3n',
    cta_sign_up: 'Registrarse',
    cta_contact_us: 'Cont\u00e1ctanos',
    cta_book_now: 'Reservar Ahora',
    cta_get_offer: 'Obtener Oferta',
    cta_subscribe: 'Suscribirse',
    lang_en: 'Ingl\u00e9s',
    lang_es: 'Espa\u00f1ol',
    leads_tab: 'Leads',
    multiplatform_tab: 'Multi-Plataforma',
    responder_tab: 'Auto Responder',
    lead_management: 'Gesti\u00f3n de Leads',
    fetch_leads: 'Obtener de Meta',
    add_lead: '+ Agregar Lead',
    new_leads: 'Nuevos',
    contacted: 'Contactados',
    converted: 'Convertidos',
    lost: 'Perdidos',
    avg_score: 'Puntaje Prom.',
    search_leads: 'Buscar leads...',
    score: 'Puntaje',
    source: 'Fuente',
    name: 'Nombre',
    email: 'Correo',
    phone: 'Tel\u00e9fono',
    notes: 'Notas',
    save_lead: 'Guardar Lead',
    workflows: 'Flujos de Trabajo',
    create_workflow: '+ Crear Flujo',
    workflow_name: 'Nombre del Flujo',
    workflow_steps: 'Pasos (array JSON)',
    save_workflow: 'Guardar Flujo',
    no_scheduled_posts: 'Sin posts programados.',
    enabled: 'Activado',
    scan_comments: 'Escanear y Responder',
    auto_responder: 'Auto Responder de Redes Sociales',
    add_rule: '+ Agregar Regla',
    use_ai: 'Usar Respuestas AI',
    default_response: 'Predeterminada:',
    auto_reply_rules: 'Reglas de Respuesta Autom\u00e1tica',
    response_log: 'Registro de Respuestas',
    from: 'De',
    comment: 'Comentario',
    response: 'Respuesta',
    no_responses: 'Sin respuestas a\u00fan.',
    keyword: 'Palabra Clave',
    save_rule: 'Guardar Regla',
    multi_platform_scheduler: 'Programador Multi-Plataforma',
    schedule_post: 'Programar Post',
    schedule: 'Programar',
    save_schedule: 'Guardar Programaci\u00f3n',
    fetching_leads: 'Obteniendo leads de Meta...',
    lead_deleted: 'Lead eliminado!',
    name_required: 'Nombre es requerido',
    lead_added: 'Lead agregado!',
    invalid_json_steps: 'JSON de pasos inv\u00e1lido',
    workflow_created: 'Flujo de trabajo creado!',
    workflow_deleted: 'Flujo de trabajo eliminado!',
    select_platform: 'Selecciona al menos una plataforma',
    deleted: 'Eliminado!',
    default_response_saved: 'Respuesta predeterminada guardada!',
    keyword_response_required: 'Palabra clave y respuesta son requeridas',
    rule_added: 'Regla agregada!',
    rule_deleted: 'Regla eliminada!',
    scanning_comments: 'Escaneando comentarios...',
    error_scanning: 'Error al escanear',
    quick_schedule: 'Programaci\u00f3n R\u00e1pida',
    single_post_hint: 'Pon una sola fecha y hora para ESTE post.',
    repeat_hint: 'Quieres repetir este mismo post automaticamente para no tener que programarlo dia a dia? Primero se guarda, y luego elige cada cuanto:',
    time_added: 'Horario a\u00f1adido',
    add_content_first: 'Agrega un mensaje o imagen primero.',
    repeat_setup_hint: 'Borrador guardado. Agrega al menos una hora abajo y este preset llenara las fechas automaticamente.',
    no_time_yet: 'Sin horario todavia. Elige fecha y hora y oprime "+ Add".',
    add_hour: '+ Hora',
    no_hours: 'Sin horas a\u00f1adidas.',
    add_hours_first: 'A\u00f1ade horas primero.',
    select_start_date: 'Selecciona una fecha de inicio.',
    select_hour_first: 'Selecciona una hora primero.',
    preset_7d: '7 D\u00edas',
    preset_weekly: '4 Semanas',
    preset_monthly: '3 Meses',
    preset_3m: '6 Meses',
    preset_12m: '12 Meses',
    manual_times: 'Horas Manuales',
    slots_generated: 'espacios generados!',
    login_title: 'Iniciar sesi\u00f3n',
    login_desc: 'Este panel tiene m\u00faltiples clientes configurados. Ingresa tus datos.',
    login_client_id: 'Cliente (tenant ID)',
    sign_in: 'Entrar',
    logout: 'salir',
    safety_header: 'IMPORTANTE:',
    safety_body: 'Al crear la campa\u00f1a, Meta la crea en estado PAUSED. Si ya tienes tarjeta configurada, revisa inmediatamente en Meta Ads Manager que Campaign, Ad Set y Ad est\u00e9n en OFF (gris). Meta puede reactivarlos solo al agregar tarjeta.',
    kpi_title: '\u00bfEst\u00e1 funcionando?',
    kpi_refresh: 'actualizar',
    kpi_cost_per_lead: 'costo/lead',
    kpi_booked: 'agend\u00f3',
    kpi_enrolled: 'se inscribe',
    remove: 'Quitar',
    optimization_goal: 'Objetivo de Optimizaci\u00f3n',
    opt_leads: 'Leads',
    opt_traffic: 'Tr\u00e1fico',
    opt_awareness: 'Notoriedad',
    opt_clicks: 'Clics en Enlace',
    opt_goal_hint: 'C\u00f3mo Meta optimiza la entrega para tu conjunto de anuncios',
    placements_hint: 'Selecciona d\u00f3nde aparecer\u00e1n tus anuncios',
    ai_instruction_hint: 'Describe lo que quieres promocionar. La IA usar\u00e1 esto para generar headlines y textos \u00fanicos.',
    no_interests_selected: 'Ning\u00fan inter\u00e9s seleccionado',
    default_business_name: 'Tu Negocio',
    placements_label: 'Ubicaciones:',
    token_unlimited: 'Token: Ilimitado',
    token_expiring: 'Token expira en {days} d\u00edas - RENUEVA',
    token_renew_soon: 'Token: {days} d\u00edas - Renueva pronto',
    token_days: 'Token: {days} d\u00edas',
    confidence_sin_datos: 'Todav\u00eda no hay leads registrados para este per\u00edodo.',
    confidence_muy_bajo: 'Menos de 10 leads \u2014 es solo una muestra inicial, no saques conclusiones todav\u00eda.',
    confidence_bajo: 'Entre 10 y 30 leads \u2014 ya empieza a ser \u00fatil, pero espera a tener m\u00e1s para decidir cambios grandes.',
    confidence_aceptable: '30+ leads \u2014 suficiente para empezar a confiar en estos n\u00fameros.',
  }
};
function _t(key) { return langData[lang] && langData[lang][key] !== undefined ? langData[lang][key] : langData.en[key] || key; }
function toggleLang() {
  lang = lang === 'en' ? 'es' : 'en';
  try { localStorage.setItem('meta_ads_lang', lang); } catch(e) {}
  document.getElementById('lang-toggle').textContent = lang === 'en' ? 'ES/EN' : 'EN/ES';
  translateDOM();
  renderInterestPresets();
}
function translateDOM() {
  document.querySelectorAll('[data-i18n]').forEach(function(el) {
    var key = el.getAttribute('data-i18n');
    el.textContent = _t(key);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(function(el) {
    var key = el.getAttribute('data-i18n-placeholder');
    el.placeholder = _t(key);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(function(el) {
    var key = el.getAttribute('data-i18n-title');
    el.title = _t(key);
  });
}
var campaigns = [];
var campaignFilter = 'all';
var campaignPage = 1;
var campaignPerPage = 10;
var currentDetailId = null;
var uploadedMediaUrl = '';

function updateRadius() {
  document.getElementById('radius-val').textContent = document.getElementById('f-radius').value + ' mi';
  updatePreview();
}

function toggleBudgetSplit() {
  var plat = document.getElementById('f-platforms').value;
  document.getElementById('budget-split-group').style.display = plat === 'both' ? '' : 'none';
  document.getElementById('placements-group').style.display = plat === 'instagram' ? 'none' : '';
}
function updatePlacements() {
  var plat = document.getElementById('f-platforms').value;
  var igOnly = document.querySelectorAll('.ig-only');
  igOnly.forEach(function(el) { el.style.display = (plat === 'both' || plat === 'instagram') ? '' : 'none'; });
}
var selectedInterests = {};

function initInterestPresets() {
  var presets = {
    service: [
      {id:'6003133616467', key:'int_local_business'},
      {id:'6003185165343', key:'int_health'},
      {id:'6003384248805', key:'int_fitness'},
      {id:'6003159036587', key:'int_sports'},
      {id:'6003186165698', key:'int_tech'}
    ],
    restaurant: [
      {id:'6003131997167', key:'int_cooking'},
      {id:'6003133616467', key:'int_local_business'},
      {id:'6003159036587', key:'int_sports'},
      {id:'6003099723744', key:'int_travel'},
      {id:'6003102610252', key:'int_fashion'}
    ],
    retail: [
      {id:'6003395060800', key:'int_online_shopping'},
      {id:'6003102610252', key:'int_fashion'},
      {id:'6003384248805', key:'int_fitness'},
      {id:'6003159036587', key:'int_sports'},
      {id:'6003186165698', key:'int_tech'}
    ],
    ecommerce: [
      {id:'6003395060800', key:'int_online_shopping'},
      {id:'6003186165698', key:'int_tech'},
      {id:'6003102610252', key:'int_fashion'},
      {id:'6003099723744', key:'int_travel'},
      {id:'6003159036587', key:'int_sports'}
    ],
    martialarts: [
      {id:'6003142844668', key:'int_martial_arts'},
      {id:'6003179178152', key:'int_jiu_jitsu'},
      {id:'6003224130745', key:'int_kickboxing'},
      {id:'6002998422994', key:'int_mma'},
      {id:'6003714104953', key:'int_self_defense'},
      {id:'6003384248805', key:'int_fitness'},
      {id:'6003159036587', key:'int_sports'}
    ],
    medical: [
      {id:'6003185165343', key:'int_health'},
      {id:'6003384248805', key:'int_fitness'},
      {id:'6003133616467', key:'int_local_business'},
      {id:'6003159036587', key:'int_sports'},
      {id:'6003186165698', key:'int_tech'}
    ]
  };
  window.interestPresets = presets;
  document.getElementById('f-industry').addEventListener('change', function() { renderInterestPresets(); });
  renderInterestPresets();
}

function renderInterestPresets() {
  var ind = document.getElementById('f-industry').value;
  var list = window.interestPresets[ind] || window.interestPresets.service;
  var html = '';
  for (var i = 0; i < list.length; i++) {
    var it = list[i];
    var label = _t(it.key);
    var checked = selectedInterests[it.id] ? 'checked' : '';
    html += '<label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;cursor:pointer;"><input type="checkbox" class="chk-interest" value="' + it.id + '" ' + checked + ' onchange="toggleInterest(' + "'" + it.id + "'" + ',' + "'" + label.replace(/'/g, "\\'") + "'" + ',this.checked)"> ' + label + '</label>';
  }
  document.getElementById('interest-presets').innerHTML = html;
  renderInterestTags();
}

function toggleInterest(id, name, checked) {
  if (checked) selectedInterests[id] = name;
  else delete selectedInterests[id];
  renderInterestTags();
}

function addCustomInterest() {
  var input = document.getElementById('f-custom-interest');
  var val = input.value.trim();
  if (!val) return;
  var parts = val.split(':');
  var name = parts.length > 1 ? parts[0].trim() : val;
  var id = parts.length > 1 ? parts[1].trim() : val;
  if (selectedInterests[id]) { showToast(_t('interest_already_added')); input.value = ''; return; }
  selectedInterests[id] = name;
  input.value = '';
  renderInterestPresets();
  renderInterestTags();
}

function removeInterest(id) {
  delete selectedInterests[id];
  renderInterestPresets();
  renderInterestTags();
}

function renderInterestTags() {
  var keys = Object.keys(selectedInterests);
  var html = '';
  for (var i = 0; i < keys.length; i++) {
    html += '<span style="display:inline-flex;align-items:center;gap:4px;background:#e7f3ff;color:#1877f2;padding:3px 8px;border-radius:12px;font-size:12px;font-weight:500;">' + selectedInterests[keys[i]] + ' <span onclick="removeInterest(' + "'" + keys[i] + "'" + ')" style="cursor:pointer;font-weight:700;font-size:14px;">&times;</span></span>';
  }
  document.getElementById('interest-tags').innerHTML = html || '<span style="font-size:12px;color:#888;">' + _t('no_interests_selected') + '</span>';
}

function getSelectedInterestIds() {
  return Object.keys(selectedInterests);
}

function updateBudgetSplit() {
  const v = parseInt(document.getElementById('f-fb-pct').value) || 60;
  document.getElementById('fb-pct-val').textContent = v;
  document.getElementById('ig-pct-val').textContent = 100 - v;
}

function updatePreview() {
  const headline = document.getElementById('f-headline').value || _t('your_headline');
  const message = document.getElementById('f-message').value || _t('your_message');
  const location = document.getElementById('f-location').value || _t('selected_location');
  const radius = document.getElementById('f-radius').value;
  const ageMin = document.getElementById('f-age-min').value;
  const ageMax = document.getElementById('f-age-max').value;
  const cta = document.getElementById('f-cta');
  const ctaLabel = cta.options[cta.selectedIndex]?.text || _t('cta_learn_more');
  const destUrl = document.getElementById('f-destination-url').value || '#';
  const bizName = document.getElementById('f-name').value || _t('default_business_name');

  document.getElementById('preview-headline').textContent = headline;
  document.getElementById('preview-message').textContent = message;
  document.getElementById('preview-cta').textContent = ctaLabel;
  document.getElementById('preview-cta').href = destUrl;
  document.getElementById('preview-page-name').textContent = bizName;
  document.getElementById('preview-avatar').textContent = bizName.split(' ').map(function(w){return w[0]}).join('').substring(0,2).toUpperCase() || 'BN';
  document.getElementById('preview-target').textContent =
    _t('location_label') + ' ' + location + ' - ' + radius + ' ' + _t('mi_radius') + ' - ' + _t('ages') + ' ' + ageMin + '-' + ageMax;

  // Update carousel preview
  const mediaArea = document.getElementById('preview-media-area');
  if (carouselUrls && carouselUrls.length > 0) {
    let imgs = '';
    carouselUrls.forEach(function(u) {
      imgs += '<img src="' + u + '" style="height:200px;max-width:150px;object-fit:cover;flex-shrink:0;border-right:2px solid #fff;">';
    });
    mediaArea.innerHTML = '<div style="display:flex;overflow-x:auto;width:100%;height:200px;">' + imgs + '</div>';
  } else if (document.getElementById('f-media-url').value) {
    const url = document.getElementById('f-media-url').value;
    mediaArea.innerHTML = '<img src="' + url + '" style="width:100%;height:200px;object-fit:cover;display:block;">';
  } else {
    mediaArea.innerHTML = '<span>' + _t('no_media') + '</span>';
  }
}

let carouselUrls = [];

async function handleMediaUpload(event) {
  const files = event.target.files;
  if (!files || files.length === 0) return;

  let isVideo = false;
  for (let f of files) {
    if (f.type.startsWith('video/')) { isVideo = true; break; }
  }

  // If any file is video, treat as single video upload
  if (isVideo) {
    const formData = new FormData();
    formData.append('media', files[0]);
    try {
      const res = await fetch('/api/upload-media', { method: 'POST', body: formData });
      const data = await res.json();
      if (data.success) {
        uploadedMediaUrl = data.url;
        document.getElementById('f-media-url').value = data.url;
        document.getElementById('f-media-urls').value = '';
        carouselUrls = [];
        document.getElementById('upload-placeholder').style.display = 'none';
        document.getElementById('upload-preview').style.display = 'block';
        document.getElementById('preview-filename').textContent = files[0].name;
        document.getElementById('carousel-thumbs').style.display = 'none';
        document.getElementById('preview-video').src = data.url;
        document.getElementById('preview-video').style.display = 'block';
        updatePreview();
      } else {
        alert('Upload failed: ' + (data.error || 'Unknown error'));
      }
    } catch (e) {
      alert('Upload error: ' + e.message);
    }
    return;
  }

  // Upload each image for carousel
  for (let f of files) {
    const formData = new FormData();
    formData.append('media', f);
    try {
      const res = await fetch('/api/upload-media', { method: 'POST', body: formData });
      const data = await res.json();
      if (data.success) {
        carouselUrls.push(data.url);
      }
    } catch (e) {
      console.error('Upload error:', e);
    }
  }

  if (carouselUrls.length > 0) {
    document.getElementById('f-media-urls').value = JSON.stringify(carouselUrls);
    document.getElementById('upload-placeholder').style.display = 'none';
    document.getElementById('upload-preview').style.display = 'block';
    document.getElementById('preview-video').style.display = 'none';
    document.getElementById('carousel-thumbs').style.display = 'flex';
    document.getElementById('img-count').textContent = carouselUrls.length;
    document.getElementById('preview-filename').textContent = carouselUrls.length + ' ' + _t('images_selected');

    // Build thumbnails
    const thumbsDiv = document.getElementById('carousel-thumbs');
    thumbsDiv.innerHTML = '';
    carouselUrls.forEach((url, i) => {
      const thumb = document.createElement('div');
      thumb.style.cssText = 'position:relative;width:80px;height:80px;border-radius:6px;overflow:hidden;border:2px solid #ddd;flex-shrink:0;';
      thumb.innerHTML = '<img src="' + url + '" style="width:100%;height:100%;object-fit:cover;">' +
        '<div onclick="removeCarouselImg(' + i + ')" style="position:absolute;top:-4px;right:-4px;width:18px;height:18px;background:#ff4444;color:white;border-radius:50%;font-size:12px;line-height:18px;text-align:center;cursor:pointer;">x</div>';
      thumbsDiv.appendChild(thumb);
    });

    updatePreview();
  }
}

function removeCarouselImg(index) {
  carouselUrls.splice(index, 1);
  document.getElementById('f-media-urls').value = JSON.stringify(carouselUrls);
  document.getElementById('img-count').textContent = carouselUrls.length;
  const thumbsDiv = document.getElementById('carousel-thumbs');
  thumbsDiv.innerHTML = '';
  carouselUrls.forEach((url, i) => {
    const thumb = document.createElement('div');
    thumb.style.cssText = 'position:relative;width:80px;height:80px;border-radius:6px;overflow:hidden;border:2px solid #ddd;flex-shrink:0;';
    thumb.innerHTML = '<img src="' + url + '" style="width:100%;height:100%;object-fit:cover;">' +
      '<div onclick="removeCarouselImg(' + i + ')" style="position:absolute;top:-4px;right:-4px;width:18px;height:18px;background:#ff4444;color:white;border-radius:50%;font-size:12px;line-height:18px;text-align:center;cursor:pointer;">x</div>';
    thumbsDiv.appendChild(thumb);
  });
  if (carouselUrls.length === 0) {
    document.getElementById('upload-placeholder').style.display = '';
    document.getElementById('upload-preview').style.display = 'none';
  }
  updatePreview();
}

function goStep(n) {
  document.querySelectorAll('.step-content').forEach(el => el.style.display = 'none');
  document.getElementById('step-' + n).style.display = 'block';
  document.querySelectorAll('.step-indicator .step').forEach(el => {
    el.classList.remove('active');
    const stepNum = parseInt(el.dataset.step);
    if (stepNum === n) el.classList.add('active');
    else if (stepNum < n) el.classList.add('done');
  });
  if (n === 4) updatePreview();
}

async function generateAdCopy() {
  const name = document.getElementById('f-name').value.trim();
  const industry = document.getElementById('f-industry').value;
  const language = document.getElementById('f-language').value;
  if (!name) { showToast(_t('enter_biz_name')); goStep(1); return; }
  try {
    const aiInstruction = document.getElementById('f-ai-instruction').value.trim();
    const res = await fetch('/api/generate-ad-copy', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ 
        business_name: name, 
        industry: industry, 
        location: document.getElementById('f-location').value || 'your area',
        description: aiInstruction || ('Promote ' + name + ' in ' + location),
        language: language
      })
    });
    const data = await res.json();
    if (data.success) {
      const h = data.headlines[0];
      const m = data.messages[0];
      document.getElementById('f-headline').value = h;
      document.getElementById('f-message').value = m;
      updatePreview();
      showToast(_t('ad_copy_generated'));
    } else {
      showToast(_t('generation_failed'));
    }
  } catch(e) { showToast(_t('error') + ': ' + e.message); }
}

function openLeadsTab() {
  loadLeadForms();
  document.getElementById('leads-section').scrollIntoView({behavior:'smooth'});
}

async function loadLeadForms() {
  try {
    const res = await fetch('/api/lead-forms');
    const data = await res.json();
    const sel = document.getElementById('lead-form-select');
    sel.innerHTML = '<option value="">' + _t('select_form') + '</option>';
    if (data.success && data.forms.length) {
      data.forms.forEach(f => {
        sel.innerHTML += '<option value="' + f.id + '">' + (f.name || 'Form ' + f.id) + '</option>';
      });
      document.getElementById('lead-no-forms').style.display = 'none';
    } else {
      document.getElementById('lead-no-forms').style.display = 'block';
    }
  } catch(e) { showToast(_t('error_loading_forms')); }
}

async function loadLeads() {
  const formId = document.getElementById('lead-form-select').value;
  if (!formId) { showToast(_t('select_form_first')); return; }
  const btn = document.getElementById('load-leads-btn');
  btn.textContent = _t('loading');
  try {
    const res = await fetch('/api/leads/' + formId);
    const data = await res.json();
    const container = document.getElementById('leads-table-container');
    if (data.success && data.leads.length) {
      const leads = data.leads;
      const allKeys = [...new Set(leads.flatMap(l => Object.keys(l.fields)))];
      let html = '<table><thead><tr><th>' + _t('date') + '</th>' + allKeys.map(k => '<th>' + k.replace(/_/g,' ') + '</th>').join('') + '</tr></thead><tbody>';
      leads.forEach(l => {
        html += '<tr><td style="font-size:.8rem;">' + (l.created_time || '-') + '</td>';
        html += allKeys.map(k => '<td>' + (l.fields[k] || '-') + '</td>').join('');
        html += '</tr>';
      });
      html += '</tbody></table>';
      container.innerHTML = html;
      document.getElementById('lead-download-btn').style.display = 'inline-block';
      document.getElementById('lead-download-btn').onclick = function() { downloadLeadsCSV(leads, allKeys); };
      document.getElementById('leads-empty').style.display = 'none';
    } else {
      container.innerHTML = '';
      document.getElementById('leads-empty').style.display = 'block';
      document.getElementById('lead-download-btn').style.display = 'none';
    }
  } catch(e) { showToast(_t('error_loading_leads')); }
  btn.textContent = _t('load_leads');
}

function downloadLeadsCSV(leads, keys) {
  let csv = 'Date,' + keys.map(k => '"' + k.replace(/_/g,' ') + '"').join(',') + '\\n';
  leads.forEach(l => {
    csv += '"' + (l.created_time || '') + '",' + keys.map(k => '"' + (l.fields[k] || '') + '"').join(',') + '\\n';
  });
  const blob = new Blob([csv], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'leads_export.csv';
  a.click();
  showToast(_t('leads_downloaded'));
}

async function loadCampaigns() {
  campaignPage = 1;
  campaignFilter = 'all';
  updateCampaignFilterUI();
  try {
    const res = await fetch('/api/campaigns');
    campaigns = await res.json();
    renderTable();
    updateStats();
  } catch(e) {
    showAlert(_t('error') + ': ' + e.message, 'error');
  }
}

function getFilteredCampaigns() {
  if (campaignFilter === 'trashed') return campaigns.filter(function(c) { return c.status === 'TRASHED'; });
  return campaigns.filter(function(c) { return c.status !== 'TRASHED'; });
}

function renderTable() {
  const filtered = getFilteredCampaigns();
  const totalPages = Math.ceil(filtered.length / campaignPerPage) || 1;
  if (campaignPage > totalPages) campaignPage = totalPages;
  const start = (campaignPage - 1) * campaignPerPage;
  const pageCampaigns = filtered.slice(start, start + campaignPerPage);

  const tbody = document.getElementById('campaigns-table');
  if (!pageCampaigns.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#65676b;padding:30px;">' + (campaignFilter === 'trashed' ? _t('no_trashed_campaigns') : _t('no_campaigns')) + '</td></tr>';
    paginateCampaigns(1);
    return;
  }
  tbody.innerHTML = pageCampaigns.map(function(c) {
    const adType = c.ad_type || c.strategy?.ad_types?.[0] || '-';
    const locationText = c.location || '-';
    const budget = c.budget_daily ? '$' + c.budget_daily + '/day' : (c.strategy?.campaign_name ? '-' : '-');
    let badge = '<span class="badge badge-active">' + c.status + '</span>';
    if (c.status === 'pending_auth' || c.ad_status === 'pending_auth') {
      badge = '<span class="badge badge-warn">Pending Auth</span>';
    }
    if (c.status === 'TRASHED') {
      badge = '<span class="badge" style="background:#dc2626;color:#fff;">' + _t('trash') + '</span>';
    }
    const cid = c.campaign_id || c.id;
    const name = c.strategy?.campaign_name || c.campaign_name || 'Unnamed';
    if (c.status === 'TRASHED') {
      return `<tr>` +
        `<td><strong>${name}</strong></td>` +
        `<td>${adType}</td>` +
        `<td>${locationText}</td>` +
        `<td>${budget}</td>` +
        `<td>${badge}</td>` +
        `<td>${c.created_at || '-'}</td>` +
        `<td>` +
          `<button class="btn btn-sm btn-primary" onclick="restoreCampaign('${cid}')">${_t('restore')}</button> ` +
          `<button class="btn btn-sm btn-danger" onclick="deleteCampaignForever('${cid}')">${_t('delete_forever')}</button> ` +
        `</td></tr>`;
    }
    return `<tr>` +
      `<td><strong>${name}</strong></td>` +
      `<td>${adType}</td>` +
      `<td>${locationText}</td>` +
      `<td>${budget}</td>` +
      `<td>${badge}</td>` +
      `<td>${c.created_at || '-'}</td>` +
      `<td>` +
        `<button class="btn btn-sm btn-primary" onclick="viewCampaign('${cid}')">${_t('view')}</button> ` +
        `<button class="btn btn-sm btn-warn" onclick="optimizeCampaign('${cid}')">${_t('optimize')}</button> ` +
        `<button class="btn btn-sm btn-outline" onclick="reuseCampaign('${cid}')">${_t('reuse')}</button> ` +
        `<button class="btn btn-sm btn-danger" onclick="deleteCampaign('${cid}')">🗑</button> ` +
      `</td></tr>`;
  }).join('');
  paginateCampaigns(totalPages);
}

function setCampaignFilter(f) {
  campaignFilter = f;
  campaignPage = 1;
  updateCampaignFilterUI();
  renderTable();
}

function updateCampaignFilterUI() {
  document.querySelectorAll('.campaign-filter-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.filter === campaignFilter);
  });
}

function paginateCampaigns(totalPages) {
  var el = document.getElementById('campaign-pagination');
  if (!el) return;
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  el.innerHTML = '<button class="btn btn-sm" onclick="campaignPage=' + Math.max(1, campaignPage-1) + ';renderTable();" style="background:#e4e6eb;"' + (campaignPage<=1?' disabled':'') + '>&laquo; ' + _t('prev') + '</button>' +
    '<span style="padding:6px 12px;font-size:13px;">' + _t('page') + ' ' + campaignPage + ' ' + _t('of') + ' ' + totalPages + '</span>' +
    '<button class="btn btn-sm" onclick="campaignPage=' + Math.min(totalPages, campaignPage+1) + ';renderTable();" style="background:#e4e6eb;"' + (campaignPage>=totalPages?' disabled':'') + '>' + _t('next') + ' &raquo;</button>';
}

function updateStats() {
  document.getElementById('stat-campaigns').textContent = campaigns.length;
  document.getElementById('stat-active').textContent = campaigns.filter(c => c.status === 'active' || c.status === 'ACTIVE').length;
  const totalBudget = campaigns.reduce((s, c) => s + (parseFloat(c.budget_daily) || 0), 0);
  document.getElementById('stat-budget').textContent = '$' + totalBudget;
  const totalLeads = campaigns.reduce((sum, c) => sum + (c.lead_forms_submit || 0), 0);
  document.getElementById('stat-leads').textContent = totalLeads;
}

async function viewDetail(campaignId) {
  currentDetailId = campaignId;
  try {
    const res = await fetch('/api/campaign/' + campaignId + '/performance');
    const data = await res.json();
    const { campaign, metrics, analysis } = data;

    document.getElementById('detail-title').textContent = campaign.strategy?.campaign_name || 'Campaign';

    const m = metrics;
    document.getElementById('perf-metrics').innerHTML = '' +
      '<div class="detail-box"><div class="val">$' + m.spend + '</div><div class="key">' + _t('spend') + '</div></div>' +
      '<div class="detail-box"><div class="val">' + m.clicks + '</div><div class="key">' + _t('clicks') + '</div></div>' +
      '<div class="detail-box"><div class="val">' + m.lead_forms_submit + '</div><div class="key">' + _t('leads') + '</div></div>' +
      '<div class="detail-box"><div class="val">' + (m.ctr*100).toFixed(1) + '%</div><div class="key">' + _t('ctr') + '</div></div>' +
      '<div class="detail-box"><div class="val">$' + m.cpc + '</div><div class="key">' + _t('cpc') + '</div></div>' +
      '<div class="detail-box"><div class="val">' + m.roas + 'x</div><div class="key">ROAS</div></div>';
    const grade = analysis.performance_grade;
    const badgeCls = grade === 'Excellent' || grade === 'Good' ? 'badge-good' : 'badge-poor';
    const opps = analysis.optimization_opportunities || [];
    document.getElementById('perf-opportunities').innerHTML = '' +
      '<p style="margin-bottom:10px;"><span class="badge ' + badgeCls + '">' + grade + '</span> Overall Grade</p>' +
      (opps.length ? '<p style="font-weight:600;margin-bottom:6px;font-size:.85rem;color:#65676b;">' + _t('opportunities') + '</p>' + opps.map(function(o) { return '<div style="padding:8px;background:#fff8e1;border-radius:6px;margin-bottom:6px;font-size:.85rem;">' + o + '</div>'; }).join('') : '<p style="color:#42b72a;font-size:.85rem;">' + _t('no_issues') + '</p>');

    const s = campaign.strategy || {};
    document.getElementById('strategy-info').innerHTML = '' +
      '<div style="display:grid;gap:10px;">' +
        '<div><strong>' + _t('objectives') + ':</strong> ' + (s.objectives||[]).join(', ') + '</div>' +
        '<div><strong>' + _t('ad_types') + ':</strong> ' + (s.ad_types||[]).join(', ') + '</div>' +
        '<div><strong>' + _t('targeting') + ':</strong> ' + (s.targeting_strategies||[]).join(', ') + '</div>' +
        '<div><strong>Est. ROAS:</strong> ' + (s.estimated_roas||0).toFixed(1) + 'x</div>' +
        '<div><strong>' + _t('bidding') + ':</strong> ' + (s.recommended_bidding?.bidding_strategy || '-') + '</div>' +
      '</div>';

    const platforms = campaign.platforms;
    const platformNames = {facebook:'Facebook', instagram:'Instagram', messenger:'Messenger', both:'Facebook & Instagram'};
    if (platforms && platforms !== 'both') {
      const showPlatforms = platforms === 'facebook' ? ['facebook'] : ['instagram'];
      document.getElementById('budget-info').innerHTML = showPlatforms.map(function(p) {
        return '<div style="margin-bottom:10px;"><div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span style="text-transform:capitalize;font-size:.85rem;">' + (platformNames[p]||p) + '</span><span style="font-weight:600;">100%</span></div><div style="background:#e4e6eb;border-radius:4px;height:8px;"><div style="background:#1877f2;width:100%;height:100%;border-radius:4px;"></div></div></div>';
      }).join('');
    } else {
      const fbPct = campaign.fb_budget_pct || 50;
      document.getElementById('budget-info').innerHTML = '<div style="margin-bottom:10px;"><div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span style="text-transform:capitalize;font-size:.85rem;">Facebook</span><span style="font-weight:600;">' + fbPct + '%</span></div><div style="background:#e4e6eb;border-radius:4px;height:8px;"><div style="background:#1877f2;width:' + fbPct + '%;height:100%;border-radius:4px;"></div></div></div><div style="margin-bottom:10px;"><div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span style="text-transform:capitalize;font-size:.85rem;">Instagram</span><span style="font-weight:600;">' + (100-fbPct) + '%</span></div><div style="background:#e4e6eb;border-radius:4px;height:8px;"><div style="background:#1877f2;width:' + (100-fbPct) + '%;height:100%;border-radius:4px;"></div></div></div>';
    }

    var pl = campaign.placements;
    if (pl && pl.length > 0) {
      var plNames = {feed:'Feed', story:'Stories', marketplace:'Marketplace', reels:'Reels', explore:'Explore', video_feeds:'Video Feed'};
      var plHtml = '<div style="margin-top:8px;font-size:.8rem;color:#65676b;">' + _t('placements_label') + ' ' + pl.map(function(v) { return plNames[v]||v; }).join(', ') + '</div>';
      document.getElementById('budget-info').innerHTML += plHtml;
    }
    document.getElementById('detail-optimize-btn').onclick = function() { optimizeCampaign(campaignId); };
    document.getElementById('detail-modal').classList.add('open');
    switchTab('performance');
  } catch(e) {
    showToast(_t('error_loading_details'));
  }
}

function closeDetail() { document.getElementById('detail-modal').classList.remove('open'); }

function viewCampaign(campaignId) {
  var c = campaigns.find(function(x) { return (x.campaign_id || x.id) === campaignId; });
  if (!c) { showToast(_t('campaign_not_found')); return; }
  var meta = c.meta_response || {};
  document.getElementById('preview-camp-name').textContent = c.strategy?.campaign_name || c.campaign_name || 'Unnamed';
  document.getElementById('preview-camp-id').textContent = c.campaign_id || c.id;
  document.getElementById('preview-camp-status').textContent = c.status;
  document.getElementById('preview-camp-objective').textContent = meta.objective || c.objective || '-';
  document.getElementById('preview-camp-created').textContent = c.created_at || '-';
  document.getElementById('preview-camp-budget').textContent = c.budget_daily ? '$' + c.budget_daily + '/day' : (meta.daily_budget ? '$' + meta.daily_budget + '/day' : '-');
  document.getElementById('preview-camp-lifetime').textContent = meta.lifetime_budget ? '$' + meta.lifetime_budget : '-';
  document.getElementById('preview-camp-targeting').textContent = c.location || c.targeting || '-';
  document.getElementById('preview-camp-adtype').textContent = c.ad_type || c.strategy?.ad_types?.[0] || meta.objective || '-';
  document.getElementById('preview-camp-platforms').textContent = c.platforms || '-';
  var strat = c.strategy || {};
  document.getElementById('preview-camp-strategy').innerHTML = strat.objectives ? strat.objectives.join(', ') : '-';
  document.getElementById('preview-optimize-btn').onclick = function() { optimizeCampaign(campaignId); };
  document.getElementById('preview-detail-btn').onclick = function() { closePreview(); viewDetail(campaignId); };
  document.getElementById('preview-modal').classList.add('open');
}

function closePreview() { document.getElementById('preview-modal').classList.remove('open'); }

function switchPage(name) {
  document.querySelectorAll('.page').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.nav-tab').forEach(function(t) { t.classList.remove('active'); });
  document.getElementById('page-' + name).classList.add('active');
  var tab = document.querySelector('.nav-tab[data-page="' + name + '"]');
  if (tab) tab.classList.add('active');
  if (name === 'reports') loadReports();
  if (name === 'settings') { loadAccounts(); loadPixel(); loadTenants(); checkAdminRole(); loadSafetyStatus(); }
  if (name === 'content') loadPosts();
  if (name === 'leads') { loadLeads(); loadWorkflows(); }
  if (name === 'multiplatform') { loadMultiQueue(); }
  if (name === 'responder') { loadResponder(); }
}

document.addEventListener('click', function(e) {
  var tab = e.target.closest('.nav-tab');
  if (tab && tab.dataset.page) switchPage(tab.dataset.page);
});

function switchTab(name) {
  var tabs = document.querySelectorAll('.tab');
  var names = ['performance','strategy','budget'];
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].classList.toggle('active', names[i] === name);
  }
  document.querySelectorAll('.tab-content').forEach(function(tc) { tc.classList.remove('active'); });
  document.getElementById('tab-' + name).classList.add('active');
}

async function loadReports() {
  try {
    const res = await fetch('/api/reports');
    const d = await res.json();
    document.getElementById('rpt-campaigns').textContent = d.campaigns || 0;
    document.getElementById('rpt-spend').textContent = '$' + (d.total_spend || 0);
    document.getElementById('rpt-leads').textContent = d.total_leads || 0;
    document.getElementById('rpt-cpl').textContent = '$' + (d.cost_per_lead || 0);
    document.getElementById('rpt-ctr').textContent = (d.ctr || 0) + '%';
    document.getElementById('rpt-cpc').textContent = '$' + (d.avg_cpc || 0);
  } catch(e) {}
  // Load per-campaign breakdown
  try {
    const r2 = await fetch('/api/campaigns');
    const camps = await r2.json();
    let html = '';
    for (const c of camps) {
      const cid = c.campaign_id || c.id;
      let spend = 0, clicks = 0, impr = 0, leads = 0;
      try {
        const r3 = await fetch('/api/campaign/' + cid + '/performance');
        const p = await r3.json();
        if (p.metrics) {
          spend = parseFloat(p.metrics.spend) || 0;
          clicks = parseInt(p.metrics.clicks) || 0;
          impr = parseInt(p.metrics.impressions) || 0;
          leads = parseInt(p.metrics.lead_forms_submit) || 0;
        }
      } catch(e) {}
      const cpl = leads > 0 ? (spend / leads).toFixed(2) : '-';
      const ctr = impr > 0 ? ((clicks/impr)*100).toFixed(2) + '%' : '-';
      html += '<tr><td>' + (c.business_name || c.name || '?') + '</td><td>$' + spend.toFixed(2) +
        '</td><td>' + clicks + '</td><td>' + impr + '</td><td>' + leads +
        '</td><td>$' + cpl + '</td><td>' + ctr + '</td></tr>';
    }
    document.getElementById('reports-table').innerHTML = html || '<tr><td colspan="7" style="text-align:center;padding:30px;color:#65676b;">' + _t('no_campaigns_reports') + '</td></tr>';
  } catch(e) {}
}

async function loadAccounts() {
  try {
    const res = await fetch('/api/accounts');
    const d = await res.json();
    let html = '';
    const accs = d.accounts || {};
    const current = d.current || '';
    for (const id in accs) {
      const a = accs[id];
      html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:10px;border:1px solid #ddd;border-radius:6px;margin-bottom:6px;">' +
        '<div><strong>' + (a.name || id) + '</strong><br><span style="font-size:.8rem;color:#65676b;">' + (a.ad_account_id || '') + '</span></div>' +
        '<div style="display:flex;gap:6px;"><button class="btn btn-sm btn-danger" onclick="deleteAccount(' + "'" + id + "'" + ')">' + _t('delete') + '</button></div></div>';
    }
    document.getElementById('accounts-list').innerHTML = html || '<div style="color:#65676b;padding:10px;">' + _t('no_additional_accounts') + '</div>';
  } catch(e) {}
}

async function saveAccount() {
  const data = {
    name: document.getElementById('acc-name').value,
    access_token: document.getElementById('acc-token').value,
    ad_account_id: document.getElementById('acc-ad-account').value,
    app_id: document.getElementById('acc-app-id').value,
    app_secret: document.getElementById('acc-app-secret').value
  };
  if (!data.name || !data.access_token || !data.ad_account_id) {
    showToast(_t('account_required')); return;
  }
  const res = await fetch('/api/accounts/save', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const r = await res.json();
  if (r.success) { showToast(_t('account_saved')); loadAccounts(); clearAccountForm(); }
  else showToast(_t('error') + ': ' + (r.error || 'unknown'));
}
function clearAccountForm() {
  document.getElementById('acc-name').value = '';
  document.getElementById('acc-token').value = '';
  document.getElementById('acc-ad-account').value = '';
  document.getElementById('acc-app-id').value = '';
  document.getElementById('acc-app-secret').value = '';
}

async function loadTenants() {
  try {
    const res = await fetch('/api/tenants');
    const d = await res.json();
    const tenants = d.tenants || {};
    const container = document.getElementById('tenants-list');
    if (!container) return;
    const keys = Object.keys(tenants);
    if (keys.length === 0) {
      container.innerHTML = '<p style="color:#65676b;">No clients yet.</p>';
      return;
    }
    var html = '';
    keys.forEach(function(tid) {
      var t = tenants[tid];
      var isDefault = tid === 'default';
      var label = t.name || tid;
      var industry = t.industry || '-';
      var budget = t.max_total_daily_budget || 50;
      html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:12px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:8px;background:#fafafa;">' +
        '<div><strong>' + _esc(label) + '</strong>' +
        ' <span style="font-size:11px;color:#65676b;">(' + _esc(tid) + ')</span>' +
        (isDefault ? ' <span class="badge" style="background:#2563eb;color:#fff;font-size:10px;">Admin</span>' : '') +
        '<br><span style="font-size:.8rem;color:#65676b;">' + _esc(industry) + ' | Budget cap: $' + budget + '/day</span></div>' +
        '<div style="display:flex;gap:6px;align-items:center;">' +
        '<a href="/?tenant=' + _esc(tid) + '" style="font-size:12px;color:#2563eb;text-decoration:none;">Open &rarr;</a>' +
        (isDefault ? '' : '<button class="btn btn-sm btn-danger" onclick="deleteTenant(\\x27' + _esc(tid) + '\\x27)">Delete</button>') +
        '</div></div>';
    });
    container.innerHTML = html;
  } catch(e) {}
}

async function createTenant() {
  var tid = document.getElementById('tenant-id').value.trim().toLowerCase().replace(/[^a-z0-9-]/g, '');
  var name = document.getElementById('tenant-name').value.trim();
  var password = document.getElementById('tenant-password').value;
  var industry = document.getElementById('tenant-industry').value;
  var metaToken = document.getElementById('tenant-meta-token').value.trim();
  var adAccount = document.getElementById('tenant-ad-account').value.trim();
  var pageToken = document.getElementById('tenant-page-token').value.trim();
  var budget = parseInt(document.getElementById('tenant-budget').value) || 50;
  var msgEl = document.getElementById('tenant-create-msg');
  if (!tid || !name || !password || !metaToken || !adAccount) {
    msgEl.style.color = '#c0392b';
    msgEl.textContent = 'Fill in: Client ID, Name, Password, Meta Token, and Ad Account ID.';
    return;
  }
  try {
    var payload = {
      tenant_id: tid,
      name: name,
      password: password,
      industry: industry,
      meta_access_token: metaToken,
      meta_ad_account_id: adAccount,
      max_total_daily_budget: budget,
      auto_budget_increase_enabled: false
    };
    if (pageToken) payload.meta_page_token = pageToken;
    var res = await fetch('/api/tenants', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    var r = await res.json();
    if (r.success) {
      msgEl.style.color = '#16a34a';
      msgEl.textContent = 'Client "' + name + '" created! Login at /?tenant=' + tid;
      document.getElementById('tenant-id').value = '';
      document.getElementById('tenant-name').value = '';
      document.getElementById('tenant-password').value = '';
      document.getElementById('tenant-meta-token').value = '';
      document.getElementById('tenant-ad-account').value = '';
      document.getElementById('tenant-page-token').value = '';
      loadTenants();
    } else {
      msgEl.style.color = '#c0392b';
      msgEl.textContent = r.error || 'Error creating client';
    }
  } catch(e) {
    msgEl.style.color = '#c0392b';
    msgEl.textContent = 'Connection error: ' + e.message;
  }
}

async function deleteTenant(tid) {
  if (!confirm('Delete client "' + tid + '"? This removes their campaigns and data.')) return;
  try {
    var res = await fetch('/api/tenants/' + tid, {method:'DELETE'});
    var r = await res.json();
    if (r.success) { showToast('Client deleted'); loadTenants(); }
    else showToast('Error: ' + (r.error || ''), true);
  } catch(e) { showToast('Error: ' + e.message, true); }
}

async function checkAdminRole() {
  try {
    var res = await fetch('/api/whoami');
    var d = await res.json();
    var panel = document.getElementById('admin-panel');
    if (panel) panel.style.display = (d.role === 'admin') ? 'block' : 'none';
  } catch(e) {}
}

async function loadSafetyStatus() {
  try {
    var res = await fetch('/api/safety-status');
    var d = await res.json();
    document.getElementById('safety-max-budget').value = d.max_total_daily_budget;
    var cb = document.getElementById('safety-auto-budget');
    cb.checked = d.auto_budget_increase_enabled;
    var lbl = document.getElementById('auto-budget-label');
    lbl.textContent = d.auto_budget_increase_enabled ? 'ON' : 'OFF';
    lbl.style.color = d.auto_budget_increase_enabled ? '#16a34a' : '#65676b';
    var slider = document.getElementById('auto-budget-slider');
    slider.style.background = d.auto_budget_increase_enabled ? '#2563eb' : '#ccc';
    var bar = document.getElementById('safety-status-bar');
    bar.innerHTML = 'Committed: <strong>$' + d.committed_daily_budget.toFixed(2) + '</strong> / $' + d.max_total_daily_budget.toFixed(2) + ' daily | Remaining: <strong>$' + d.remaining_daily_budget.toFixed(2) + '</strong>';
  } catch(e) {}
}

async function updateSafetyBudget() {
  var val = parseFloat(document.getElementById('safety-max-budget').value);
  if (!val || val < 1) return;
  try {
    var res = await fetch('/api/safety/update-budget', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({max_total_daily_budget:val})});
    var r = await res.json();
    if (r.success) { showToast('Budget updated'); loadSafetyStatus(); }
  } catch(e) {}
}

async function toggleAutoBudget() {
  try {
    var res = await fetch('/api/safety/toggle-auto-budget', {method:'POST'});
    var r = await res.json();
    if (r.success) loadSafetyStatus();
  } catch(e) {}
}
async function deleteAccount(id) {
  if (!confirm(_t('confirm_delete_account'))) return;
  const res = await fetch('/api/accounts/delete/' + id, {method:'POST'});
  const r = await res.json();
  if (r.success) { showToast(_t('account_deleted')); loadAccounts(); }
}

async function loadPixel() {
  try {
    const res = await fetch('/api/pixel');
    const d = await res.json();
    if (d.pixel_id) document.getElementById('pixel-id').value = d.pixel_id;
  } catch(e) {}
}
async function savePixel() {
  const pixelId = document.getElementById('pixel-id').value.trim();
  const res = await fetch('/api/pixel', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pixel_id: pixelId})});
  const r = await res.json();
  showToast(r.success ? _t('pixel_saved') : _t('error_saving_pixel'));
}

function openCreateModal() {
  document.getElementById('create-modal').classList.add('open');
  goStep(1);
  toggleBudgetSplit();
  updatePlacements();
  selectedInterests = {};
  renderInterestPresets();
}

function closeCreateModal() {
  document.getElementById('create-modal').classList.remove('open');
  document.getElementById('create-alert').innerHTML = '';
}

async function submitCampaign() {
  const safetyConfirm = document.getElementById('safety-confirm');
  if (safetyConfirm && !safetyConfirm.checked) {
    document.getElementById('create-alert').innerHTML = '<div class="alert alert-error">' + _t('safety_alert') + '</div>';
    goStep(4);
    return;
  }

  const name = document.getElementById('f-name').value.trim();
  if (!name) {
    goStep(1);
    document.getElementById('create-alert').innerHTML = '<div class="alert alert-error">' + _t('biz_name_required') + '</div>';
    return;
  }

  const btn = document.getElementById('submit-btn');
  btn.textContent = _t('creating');
  btn.disabled = true;

  const mediaUrlsField = document.getElementById('f-media-urls').value;
  const mediaUrls = mediaUrlsField ? JSON.parse(mediaUrlsField) : [];
  const payload = {
    business_name: name,
    industry: document.getElementById('f-industry').value,
    business_size: document.getElementById('f-size').value,
    location: document.getElementById('f-location').value,
    website: document.getElementById('f-website').value,
    headline: document.getElementById('f-headline').value,
    message: document.getElementById('f-message').value,
    cta: document.getElementById('f-cta').value,
    destination_url: document.getElementById('f-destination-url').value,
    radius: parseInt(document.getElementById('f-radius').value) || 25,
    age_min: parseInt(document.getElementById('f-age-min').value) || 18,
    age_max: parseInt(document.getElementById('f-age-max').value) || 65,
    platforms: document.getElementById('f-platforms').value,
    placements: Array.from(document.querySelectorAll('#placements-group input[type=checkbox]:checked')).map(function(cb) { return cb.value; }),
    fb_budget_pct: parseInt(document.getElementById('f-fb-pct').value) || 60,
    gender: document.getElementById('f-gender').value,
    interests: getSelectedInterestIds(),
    has_children: document.getElementById('f-has-children').value,
    optimization_goal: document.getElementById('f-optimization-goal').value,
    media_url: document.getElementById('f-media-url').value,
    media_urls: mediaUrls.length > 0 ? mediaUrls : undefined,
    budget: {
      daily: parseFloat(document.getElementById('f-daily').value) || 50,
      monthly: parseFloat(document.getElementById('f-monthly').value) || 1500
    }
  };

  try {
    const res = await fetch('/api/create-campaign', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const data = await res.json();
    if (data.success) {
      closeCreateModal();
      showToast(_t('campaign_created'));
      loadCampaigns();
    } else {
      document.getElementById('create-alert').innerHTML = '<div class="alert alert-error">' + (data.error || _t('error_creating')) + '</div>';
    }
  } catch(e) {
    document.getElementById('create-alert').innerHTML = '<div class="alert alert-error">' + e.message + '</div>';
  }
  btn.textContent = _t('submit');
  btn.disabled = false;
}

async function optimizeCampaign(id) {
  const preview = await fetch('/api/optimize/' + id);
  const plan = await preview.json();
  if (!plan.success || !plan.optimizations || !plan.optimizations.length) {
    showToast(_t('no_optimizations'));
    return;
  }
  const msg = _t('optimize_confirm') + '\\n\\n' + plan.optimizations.map(function(o) {
    const desc = {
      adjust_budget_allocation: _t('opt_adjust_budget'),
      test_new_audiences: _t('opt_test_audiences'),
      refresh_ad_creative: _t('opt_refresh_creative'),
      optimize_targeting: _t('opt_optimize_targeting'),
      improve_ad_copy: _t('opt_improve_copy'),
      scale_successful_elements: _t('opt_scale'),
      increase_budget: _t('opt_increase_budget'),
      expand_successful_targeting: _t('opt_expand_targeting'),
      duplicate_successful_creative: _t('opt_duplicate')
    };
    return '• ' + (desc[o] || o);
  }).join('\\n');
  if (!confirm(msg)) return;
  showToast(_t('optimizing'));
  const res = await fetch('/api/optimize/' + id, {method:'POST'});
  const data = await res.json();
  if (data.success && data.results && data.results.length) {
    showAlert(data.results.join('<br>'), 'success');
  } else if (data.success) {
    showToast(_t('campaign_optimized'));
  } else {
    showToast(_t('error') + ': ' + (data.error||'unknown'));
  }
}

async function deleteCampaign(cid) {
  if (!confirm(_t('confirm_delete_campaign'))) return;
  const res = await fetch('/api/delete-campaign/' + cid, {method:'POST'});
  const data = await res.json();
  if (data.success) {
    showToast(_t('campaign_deleted'));
    loadCampaigns();
  } else {
    showToast(_t('error') + ': ' + (data.error||'unknown'));
  }
}

async function restoreCampaign(cid) {
  const res = await fetch('/api/restore-campaign/' + cid, {method:'POST'});
  const data = await res.json();
  if (data.success) {
    showToast(_t('campaign_restored'));
    loadCampaigns();
  } else {
    showToast(_t('error') + ': ' + (data.error||'unknown'));
  }
}

async function deleteCampaignForever(cid) {
  if (!confirm(_t('confirm_delete_forever'))) return;
  const res = await fetch('/api/delete-campaign-forever/' + cid, {method:'POST'});
  const data = await res.json();
  if (data.success) {
    showToast(_t('campaign_deleted'));
    loadCampaigns();
  } else {
    showToast(_t('error') + ': ' + (data.error||'unknown'));
  }
}

async function reuseCampaign(cid) {
  const c = campaigns.find(x => (x.campaign_id||x.id) === cid);
  if (!c) { showToast(_t('error') + ': ' + _t('campaign_not_found')); return; }
  document.getElementById('f-name').value = c.business_name || '';
  document.getElementById('f-industry').value = c.industry || 'service';
  document.getElementById('f-size').value = c.business_size || 'small';
  document.getElementById('f-location').value = c.location || '';
  document.getElementById('f-radius').value = c.radius || 25;
  updateRadius();
  document.getElementById('f-age-min').value = c.age_min || 18;
  document.getElementById('f-age-max').value = c.age_max || 65;
  document.getElementById('f-headline').value = c.headline || '';
  document.getElementById('f-message').value = c.message || '';
  document.getElementById('f-website').value = c.destination_url || '';
  document.getElementById('f-daily').value = c.budget_daily || 50;
  document.getElementById('f-monthly').value = (c.budget_daily||50) * 30;
  if (c.fb_budget_pct) document.getElementById('f-fb-pct').value = c.fb_budget_pct;
  updateBudgetSplit();
  const ctaSel = document.getElementById('f-cta');
  for (let i = 0; i < ctaSel.options.length; i++) {
    if (ctaSel.options[i].value === c.cta) { ctaSel.selectedIndex = i; break; }
  }
  selectedInterests = {};
  if (c.interests && c.interests.length) {
    for (var j = 0; j < c.interests.length; j++) {
      selectedInterests[c.interests[j]] = 'Custom';
    }
  }
  renderInterestPresets();
  openCreateModal();
  showToast(_t('campaign_loaded'));
}

async function optimizeAll() {
  showToast(_t('optimizing'));
  await fetch('/api/optimize-all', {method:'POST'});
  showToast(_t('optimized'));
}

function showToast(msg) {
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(function() { t.classList.remove('show'); }, 3000);
}

function showAlert(msg, type) {
  if (!type) type = 'success';
  document.getElementById('alert-area').innerHTML = '<div class="alert alert-' + type + '" style="margin-bottom:16px;">' + msg + '</div>';
  setTimeout(function() { document.getElementById('alert-area').innerHTML = ''; }, 5000);
}

async function checkTokenStatus() {
  try {
    const res = await fetch('/api/token-status');
    const data = await res.json();
    const el = document.getElementById('token-status');
    if (data.expires_at) {
      const days = Math.floor((data.expires_at * 1000 - Date.now()) / (1000*60*60*24));
      if (days > 365) {
        el.innerHTML = _t('token_unlimited');
      } else if (days < 7) {
        el.innerHTML = _t('token_expiring').replace('{days}', days);
        el.style.color = '#ffcc00';
        el.style.fontWeight = 'bold';
      } else if (days < 30) {
        el.innerHTML = _t('token_renew_soon').replace('{days}', days);
        el.style.color = '#ffcc00';
      } else {
        el.innerHTML = _t('token_days').replace('{days}', days);
      }
    } else {
      el.innerHTML = _t('token_unlimited');
    }
  } catch(e) {}
}

translateDOM();
  document.getElementById('lang-toggle').textContent = lang === 'en' ? 'ES/EN' : 'EN/ES';
  loadCampaigns();
  initInterestPresets();
  checkTokenStatus();
  loadLeadForms();

  // Safety checkbox enable/disable submit button
  const safetyCheckbox = document.getElementById('safety-confirm');
  const submitBtn = document.getElementById('submit-btn');
  if (safetyCheckbox && submitBtn) {
    safetyCheckbox.addEventListener('change', function() {
      submitBtn.disabled = !this.checked;
    });
  }

  // Fix Ads Manager link with actual ad account
  var adsLink = document.getElementById('ads-manager-link');
  if (adsLink) {
    var actId = ((function() { try { return localStorage.getItem('meta_ad_account'); } catch(e) { return null; } })() || '1392277821202782').replace('act_','');
    adsLink.href = 'https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=' + actId;
  }

  // Content Scheduler JS ==========================================
  function _esc(s) { if (!s) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  var calDate = new Date();
  var calYear = calDate.getFullYear();
  var calMonth = calDate.getMonth();

  var allPosts = [];
  var postFilter = 'all';
  var postPage = 1;
  var postPerPage = 10;

  function getFilteredPosts() {
    var plat = document.getElementById('post-platform-filter').value;
    var q = (document.getElementById('post-search').value || '').toLowerCase();
    var filtered = allPosts;
    if (postFilter !== 'all') filtered = filtered.filter(function(p) { return p.status === postFilter; });
    if (plat !== 'all') filtered = filtered.filter(function(p) { return p.platform === plat; });
    if (q) filtered = filtered.filter(function(p) { return (p.message || '').toLowerCase().indexOf(q) !== -1; });
    return filtered;
  }

  function loadPosts() {
    fetch('/api/posts').then(function(r) { return r.json(); }).then(function(d) {
      allPosts = d.posts || [];
      renderFilteredPosts();
      renderCalendar(allPosts);
    }).catch(function() {});
  }

  function setPostFilter(f) {
    postFilter = f;
    postPage = 1;
    document.querySelectorAll('.post-tab').forEach(function(b) { b.classList.remove('active'); });
    document.querySelector('.post-tab[data-filter="' + f + '"]').classList.add('active');
    renderFilteredPosts();
  }

  function renderFilteredPosts() {
    var filtered = getFilteredPosts();
    var totalPages = Math.ceil(filtered.length / postPerPage) || 1;
    if (postPage > totalPages) postPage = totalPages;
    var start = (postPage - 1) * postPerPage;
    var pagePosts = filtered.slice(start, start + postPerPage);
    document.getElementById('post-count').textContent = filtered.length + ' ' + _t('posts') + (filtered.length !== allPosts.length ? ' (' + _t('filtered_from') + ' ' + allPosts.length + ')' : '');
    var html = '';
    function fmtDT(v) { return v ? new Date(v).toLocaleDateString() + ' ' + new Date(v).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}) : '-'; }
    if (pagePosts.length === 0) {
      html = '<tr><td colspan="6" style="text-align:center;color:#65676b;padding:30px;">' + _t('no_matches') + '</td></tr>';
    } else {
      for (var i = 0; i < pagePosts.length; i++) {
        var p = pagePosts[i];
        var platformIcon = p.platform === 'instagram' ? '📷 IG' : '📘 FB';
        var typeIcon = p.content_type === 'video' ? '🎬' : p.content_type === 'carousel' ? '📑' : '🖼️';
        var statusBadge = p.status === 'published' ? '<span style="color:#16a34a;font-weight:700;">' + _t('published') + '</span>' :
                          p.status === 'scheduled' ? '<span style="color:#1877f2;font-weight:700;">' + _t('scheduled') + '</span>' :
                          p.status === 'draft' ? '<span style="color:#f59e0b;font-weight:700;">' + _t('drafts') + '</span>' :
                          '<span style="color:#dc2626;font-weight:700;">🗑 ' + _t('trash') + '</span>';
        var sched = p.status === 'published' ? fmtDT(p.published_at) : (p.scheduled_time ? fmtDT(p.scheduled_time) : '-');
        var msg = (p.message || '').substring(0, 60) + ((p.message || '').length > 60 ? '...' : '');
        var actionsHtml = '';
        if (p.status === 'trashed') {
          actionsHtml = '<button class="btn btn-sm btn-primary" onclick="restorePost(' + "'" + p.id + "'" + ')" style="margin-right:4px;">' + _t('restore') + '</button>' +
            '<button class="btn btn-sm btn-danger" onclick="deleteForever(' + "'" + p.id + "'" + ')">' + _t('delete_forever') + '</button>';
        } else {
          if (p.status === 'draft') {
            actionsHtml = '<button class="btn btn-sm btn-success" onclick="publishPost(' + "'" + p.id + "'" + ')" style="margin-right:4px;">' + _t('publish') + '</button>';
          } else if (p.status === 'scheduled') {
            actionsHtml = '<button class="btn btn-sm btn-primary" onclick="publishPost(' + "'" + p.id + "'" + ')" style="margin-right:4px;">' + _t('publish_now') + '</button>';
          }
          actionsHtml += '<button class="btn btn-sm" onclick="showPostDetail(' + "'" + p.id + "'" + ')" style="margin-right:4px;background:#e4e6eb;">' + _t('view') + '</button>';
          actionsHtml += '<button class="btn btn-sm btn-primary" onclick="openRepeatModal(' + "'" + p.id + "'" + ')" style="margin-right:4px;" title="' + _t('repeat') + '">🔁</button>';
          actionsHtml += '<button class="btn btn-sm btn-warn" onclick="deletePost(' + "'" + p.id + "'" + ')">🗑</button>';
        }
        html += '<tr><td>' + typeIcon + '</td><td>' + platformIcon + '</td><td style="cursor:pointer;" onclick="showPostDetail(' + "'" + p.id + "'" + ')">' + _esc(msg) + '</td><td>' + sched + '</td><td>' + statusBadge + '</td><td>' + actionsHtml + '</td></tr>';
      }
    }
    var el = document.getElementById('posts-table');
    if (el) el.innerHTML = html;
    var pg = document.getElementById('post-pagination');
    if (pg) {
      if (totalPages <= 1) { pg.innerHTML = ''; return; }
      pg.innerHTML = '<button class="btn btn-sm" onclick="postPage=' + Math.max(1, postPage-1) + ';renderFilteredPosts();" style="background:#e4e6eb;"' + (postPage<=1?' disabled':'') + '>&laquo; ' + _t('prev') + '</button>' +
        '<span style="padding:6px 12px;font-size:13px;">' + _t('page') + ' ' + postPage + ' ' + _t('of') + ' ' + totalPages + '</span>' +
        '<button class="btn btn-sm" onclick="postPage=' + Math.min(totalPages, postPage+1) + ';renderFilteredPosts();" style="background:#e4e6eb;"' + (postPage>=totalPages?' disabled':'') + '>' + _t('next') + ' &raquo;</button>';
    }
  }

  function renderCalendar(posts) {
    var grid = document.getElementById('calendar-grid');
    var label = document.getElementById('cal-month-label');
    if (!grid) return;
    var months = [_t('january'),_t('february'),_t('march'),_t('april'),_t('may'),_t('june'),_t('july'),_t('august'),_t('september'),_t('october'),_t('november'),_t('december')];
    if (label) label.textContent = months[calMonth] + ' ' + calYear;
    var firstDay = new Date(calYear, calMonth, 1).getDay();
    var daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    var today = new Date();
    var html = '<div style="font-weight:700;text-align:center;color:#65676b;font-size:.75rem;padding:4px;">' + _t('sun') + '</div><div style="font-weight:700;text-align:center;color:#65676b;font-size:.75rem;padding:4px;">' + _t('mon') + '</div><div style="font-weight:700;text-align:center;color:#65676b;font-size:.75rem;padding:4px;">' + _t('tue') + '</div><div style="font-weight:700;text-align:center;color:#65676b;font-size:.75rem;padding:4px;">' + _t('wed') + '</div><div style="font-weight:700;text-align:center;color:#65676b;font-size:.75rem;padding:4px;">' + _t('thu') + '</div><div style="font-weight:700;text-align:center;color:#65676b;font-size:.75rem;padding:4px;">' + _t('fri') + '</div><div style="font-weight:700;text-align:center;color:#65676b;font-size:.75rem;padding:4px;">' + _t('sat') + '</div>';
    for (var i = 0; i < firstDay; i++) {
      html += '<div class="cal-day-box other-month"><div class="day-num"></div></div>';
    }
    for (var d = 1; d <= daysInMonth; d++) {
      var dateStr = calYear + '-' + String(calMonth + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
      var isToday = calYear === today.getFullYear() && calMonth === today.getMonth() && d === today.getDate();
      var cls = 'cal-day-box';
      if (isToday) cls += ' today';
      html += '<div class="' + cls + '"><div class="day-num">' + d + '</div>';
      for (var pi = 0; pi < posts.length; pi++) {
        var p = posts[pi];
        var pDate = p.scheduled_time ? p.scheduled_time.split('T')[0] : null;
        if (pDate === dateStr || (!pDate && p.created_at && p.created_at.split(' ')[0] === dateStr)) {
          var chipCls = 'cal-post-chip ' + p.platform + ' ' + p.status;
          var chipLabel = (p.platform === 'instagram' ? '📷' : '📘') + ' ' + (p.message || '').substring(0, 15) + ((p.message || '').length > 15 ? '..' : '');
          html += '<div class="' + chipCls + '" onclick="showPostDetail(' + "'" + p.id + "'" + ')" title="' + _esc(p.message || '') + '">' + chipLabel + '</div>';
        }
      }
      html += '</div>';
    }
    grid.innerHTML = html;
  }

  function calPrevMonth() {
    if (calMonth === 0) { calMonth = 11; calYear--; }
    else calMonth--;
    loadPosts();
  }

  function calNextMonth() {
    if (calMonth === 11) { calMonth = 0; calYear++; }
    else calMonth++;
    loadPosts();
  }

  function openCreatePostModal() {
    try {
      // Reset platform checkboxes: default Facebook checked, Instagram unchecked
      var fbCb = document.getElementById('cp-platform-fb');
      var igCb = document.getElementById('cp-platform-ig');
      if (fbCb) fbCb.checked = true;
      if (igCb) igCb.checked = false;
      var warn = document.getElementById('cp-platform-warning');
      if (warn) warn.style.display = 'none';
      document.getElementById('cp-headline').value = '';
      document.getElementById('cp-message').value = '';
      document.getElementById('cp-ai-instruction').value = '';
      document.getElementById('cp-cta').value = 'LEARN_MORE';
      document.getElementById('cp-link').value = '';
      document.getElementById('cp-schedule-start-date').value = '';
      document.getElementById('cp-schedule-hour').value = '12';
      document.getElementById('cp-schedule-min').value = '00';
      document.getElementById('cp-schedule-ampm').value = 'AM';
      document.getElementById('cp-schedule-confirm').style.display = 'none';
      document.getElementById('cp-schedule-manual-empty').style.display = '';
      document.getElementById('cp-schedule').value = '';
      document.getElementById('cp-status').innerHTML = '';
      resetCpPreview();
      document.getElementById('create-post-modal').classList.add('open');
    } catch(e) {
      console.error('Error in openCreatePostModal:', e);
      document.getElementById('create-post-modal').classList.add('open');
    }
  }

  function closeCreatePostModal() {
    try {
      document.getElementById('create-post-modal').classList.remove('open');
    } catch(e) {}
  }

  // Repeat Schedule functions
  var _rptTimeIndex = 0;
  var _rptHourIndex = 0;
  var _cpRepeatPendingDays = null;
  function openRepeatModal(sourceId, onReady) {
    _cpRepeatPendingDays = null;
    fetch('/api/posts/' + sourceId).then(function(r) { return r.json(); }).then(function(d) {
      if (!d.post) { showToast(_t('error')); return; }
      var p = d.post;
      document.getElementById('rpt-source-id').value = sourceId;
      document.getElementById('rpt-source-type').value = 'post';
      document.getElementById('rpt-headline').value = p.headline || '';
      document.getElementById('rpt-message').value = p.message || '';
      document.getElementById('rpt-ai-instruction').value = p.ai_instruction || '';
      document.getElementById('rpt-cta').value = p.cta || 'LEARN_MORE';
      document.getElementById('rpt-link').value = p.link_url || '';
      var mediaInfo = '';
      if (p.media_url || p.media_file) mediaInfo = '📎 ' + _t('media') + ': ' + (p.media_url || p.media_file);
      else if (p.media_urls && p.media_urls.length) mediaInfo = '📑 ' + p.media_urls.length + ' ' + _t('images');
      else mediaInfo = _t('no_media');
      document.getElementById('rpt-media-info').textContent = mediaInfo;
      resetRptPreview();
      document.getElementById('rpt-times-list').innerHTML = '';
      document.getElementById('rpt-times-empty').style.display = '';
      document.getElementById('rpt-hours-list').innerHTML = '';
      document.getElementById('rpt-hours-empty').style.display = '';
      document.getElementById('rpt-start-date').value = '';
      document.getElementById('rpt-status').innerHTML = '';
      _rptTimeIndex = 0;
      _rptHourIndex = 0;
      document.getElementById('repeat-modal').classList.add('open');
      if (typeof onReady === 'function') onReady();
    }).catch(function() { showToast(_t('error')); });
  }

  function openMultiRepeatModal(itemId) {
    fetch('/api/multi-scheduler/item/' + itemId).then(function(r) { return r.json(); }).then(function(d) {
      if (!d.item) { showToast(_t('error')); return; }
      var item = d.item;
      document.getElementById('rpt-source-id').value = itemId;
      document.getElementById('rpt-source-type').value = 'multi';
      document.getElementById('rpt-headline').value = item.headline || '';
      document.getElementById('rpt-message').value = item.message || '';
      document.getElementById('rpt-ai-instruction').value = item.ai_instruction || '';
      document.getElementById('rpt-cta').value = item.cta || 'LEARN_MORE';
      document.getElementById('rpt-link').value = item.link_url || '';
      var mediaInfo = '';
      if (item.media_file || (item.media_files && item.media_files.length)) mediaInfo = '📎 ' + _t('media');
      else if (item.media_urls && item.media_urls.length) mediaInfo = '📑 ' + item.media_urls.length + ' ' + _t('images');
      else mediaInfo = _t('no_media');
      document.getElementById('rpt-media-info').textContent = mediaInfo;
      resetRptPreview();
      document.getElementById('rpt-times-list').innerHTML = '';
      document.getElementById('rpt-times-empty').style.display = '';
      document.getElementById('rpt-hours-list').innerHTML = '';
      document.getElementById('rpt-hours-empty').style.display = '';
      document.getElementById('rpt-start-date').value = '';
      document.getElementById('rpt-status').innerHTML = '';
      _rptTimeIndex = 0;
      _rptHourIndex = 0;
      document.getElementById('repeat-modal').classList.add('open');
    }).catch(function() { showToast(_t('error')); });
  }

  function closeRepeatModal() {
    document.getElementById('repeat-modal').classList.remove('open');
  }

  function addRepeatHour() {
    var h = parseInt(document.getElementById('rpt-hour-select').value, 10);
    var m = document.getElementById('rpt-minute-select').value;
    var ampm = document.getElementById('rpt-ampm-select').value;
    if (isNaN(h)) { showToast(_t('select_hour_first')); return; }
    var h24 = (ampm === 'PM' && h !== 12) ? h + 12 : (ampm === 'AM' && h === 12 ? 0 : h);
    var hour24Str = String(h24).padStart(2, '0');
    var display = h + ':' + m + ' ' + ampm;
    document.getElementById('rpt-hours-empty').style.display = 'none';
    var idx = _rptHourIndex++;
    var container = document.getElementById('rpt-hours-list');
    var span = document.createElement('span');
    span.id = 'rpt-hour-' + idx;
    span.setAttribute('data-24h', hour24Str + ':' + m);
    span.style.cssText = 'display:inline-flex;align-items:center;gap:4px;background:#e7f3ff;padding:4px 10px;border-radius:12px;font-size:.85rem;';
    span.innerHTML = display + ' <span style="cursor:pointer;color:#dc2626;font-weight:700;" onclick="removeRepeatHour(' + idx + ')">x</span>';
    container.appendChild(span);
    if (_cpRepeatPendingDays && document.getElementById('rpt-start-date').value) {
      var pendingDays = _cpRepeatPendingDays;
      _cpRepeatPendingDays = null;
      generateRepeatPreset(pendingDays);
    }
  }

  function removeRepeatHour(idx) {
    var el = document.getElementById('rpt-hour-' + idx);
    if (el) el.remove();
    var container = document.getElementById('rpt-hours-list');
    if (container.children.length === 0) {
      document.getElementById('rpt-hours-empty').style.display = '';
    }
  }

  function generateRepeatPreset(totalDays) {
    var startDate = document.getElementById('rpt-start-date').value;
    if (!startDate) { showToast(_t('select_start_date')); return; }
    var hourSpans = document.getElementById('rpt-hours-list').children;
    if (!hourSpans.length) { showToast(_t('add_hours_first')); return; }
    var hours = [];
    for (var i = 0; i < hourSpans.length; i++) {
      var h24 = hourSpans[i].getAttribute('data-24h');
      if (h24) hours.push(h24);
    }
    if (!hours.length) { showToast(_t('add_hours_first')); return; }
    var container = document.getElementById('rpt-times-list');
    document.getElementById('rpt-times-empty').style.display = 'none';
    var count = 0;
    for (var d = 0; d < totalDays; d++) {
      var dateObj = new Date(startDate + 'T00:00:00');
      dateObj.setDate(dateObj.getDate() + d);
      var y = dateObj.getFullYear();
      var m = String(dateObj.getMonth() + 1).padStart(2, '0');
      var day = String(dateObj.getDate()).padStart(2, '0');
      var dateStr = y + '-' + m + '-' + day;
      for (var h = 0; h < hours.length; h++) {
        var idx = _rptTimeIndex++;
        var div = document.createElement('div');
        div.id = 'rpt-time-' + idx;
        div.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:6px;';
        div.innerHTML = '<input type="datetime-local" id="rpt-dt-' + idx + '" value="' + dateStr + 'T' + hours[h] + '" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">' +
          '<button class="btn btn-sm btn-danger" onclick="removeRepeatTime(' + idx + ')" title="' + _t('remove_time') + '">x</button>';
        container.appendChild(div);
        count++;
      }
    }
    showToast(count + ' ' + _t('slots_generated'));
  }

  function addRepeatTime() {
    var container = document.getElementById('rpt-times-list');
    document.getElementById('rpt-times-empty').style.display = 'none';
    var idx = _rptTimeIndex++;
    var div = document.createElement('div');
    div.id = 'rpt-time-' + idx;
    div.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:6px;';
    div.innerHTML = '<input type="datetime-local" id="rpt-dt-' + idx + '" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">' +
      '<button class="btn btn-sm btn-danger" onclick="removeRepeatTime(' + idx + ')" title="' + _t('remove_time') + '">x</button>';
    container.appendChild(div);
  }

  function removeRepeatTime(idx) {
    var el = document.getElementById('rpt-time-' + idx);
    if (el) el.remove();
    var container = document.getElementById('rpt-times-list');
    if (container.children.length === 0) {
      document.getElementById('rpt-times-empty').style.display = '';
    }
  }

  function getAllRepeatTimes() {
    var times = [];
    var container = document.getElementById('rpt-times-list');
    for (var i = 0; i < container.children.length; i++) {
      var inputId = container.children[i].querySelector('input[type="datetime-local"]').id;
      var val = document.getElementById(inputId).value;
      if (val) times.push(val);
    }
    return times;
  }

  function scheduleRepeatCopies() {
    var times = getAllRepeatTimes();
    if (!times.length) { showToast(_t('select_schedule')); return; }
    var sourceId = document.getElementById('rpt-source-id').value;
    var sourceType = document.getElementById('rpt-source-type').value;
    var statusEl = document.getElementById('rpt-status');
    statusEl.innerHTML = _t('saving') + '...';
    var payload = {
      source_id: sourceId,
      source_type: sourceType,
      headline: document.getElementById('rpt-headline').value.trim(),
      message: document.getElementById('rpt-message').value.trim(),
      ai_instruction: document.getElementById('rpt-ai-instruction').value.trim(),
      cta: document.getElementById('rpt-cta').value,
      link_url: document.getElementById('rpt-link').value.trim(),
      times: times
    };
    var url = sourceType === 'multi' ? '/api/multi-scheduler/repeat' : '/api/posts/repeat';
    fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) {
          statusEl.innerHTML = _t('scheduled');
          showToast(times.length + ' ' + _t('posts') + ' ' + _t('scheduled'));
          setTimeout(function() { closeRepeatModal(); loadPosts(); if (typeof loadMultiQueue === 'function') loadMultiQueue(); }, 1000);
        } else {
          statusEl.innerHTML = _t('error') + ': ' + (d.error || '');
        }
      }).catch(function(e) { statusEl.innerHTML = 'Error: ' + e.message; });
  }

  function toggleCpMedia() {
    var fbChecked = document.getElementById('cp-platform-fb') && document.getElementById('cp-platform-fb').checked;
    var igChecked = document.getElementById('cp-platform-ig') && document.getElementById('cp-platform-ig').checked;
    var linkGroup = document.getElementById('cp-link-group');
    // Hide link URL only if Instagram-only (Instagram doesn't support link in posts)
    linkGroup.style.display = (igChecked && !fbChecked) ? 'none' : '';
  }
  function resetCpPreview() {
    document.getElementById('cp-upload-preview').style.display = 'none';
    document.getElementById('cp-upload-placeholder').style.display = '';
    document.getElementById('cp-carousel-thumbs').innerHTML = '';
    document.getElementById('cp-preview-video').style.display = 'none';
    document.getElementById('cp-preview-video').src = '';
    document.getElementById('cp-preview-filename').textContent = '';
    document.getElementById('cp-img-count').textContent = '0';
    _cpUploadedMedia = '';
    _cpCarouselUrls = [];
  }

  var _cpUploadedMedia = '';
  var _cpCarouselUrls = [];

  // cp-media-input onchange is handled inline via HTML attribute

  function handleCpMediaUpload(files) {
    if (!files || files.length === 0) return;
    var statusEl = document.getElementById('cp-status');
    var isVideo = files[0].type.startsWith('video/');
    _cpUploadedMedia = '';
    _cpCarouselUrls = [];
    if (isVideo) {
      var fd = new FormData();
      fd.append('media', files[0]);
      statusEl.innerHTML = _t('uploading_video');
      fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) {
          _cpUploadedMedia = d.url;
          document.getElementById('cp-upload-placeholder').style.display = 'none';
          document.getElementById('cp-upload-preview').style.display = '';
          document.getElementById('cp-preview-video').src = d.url;
          document.getElementById('cp-preview-video').style.display = 'block';
          document.getElementById('cp-preview-filename').textContent = files[0].name;
          statusEl.innerHTML = _t('video_uploaded');
        } else { statusEl.innerHTML = _t('upload_failed'); }
      }).catch(function() { statusEl.innerHTML = _t('upload_error'); });
    } else {
      var uploadNext = function(i) {
        if (i >= files.length) {
          document.getElementById('cp-upload-placeholder').style.display = 'none';
          document.getElementById('cp-upload-preview').style.display = '';
          document.getElementById('cp-img-count').textContent = files.length;
          document.getElementById('cp-preview-filename').textContent = files.length + ' ' + _t('selected');
          var thumbs = document.getElementById('cp-carousel-thumbs');
          thumbs.innerHTML = _cpCarouselUrls.map(function(u) {
            return '<div style="position:relative;display:inline-block;"><img src="' + u + '" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:2px solid #ddd;"></div>';
          }).join('');
          statusEl.innerHTML = _cpCarouselUrls.length + ' ' + _t('images_uploaded');
          return;
        }
        var fd = new FormData();
        fd.append('media', files[i]);
        statusEl.innerHTML = _t('uploading') + ' ' + (i+1) + '/' + files.length;
        fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
          if (d.success) { _cpCarouselUrls.push(d.url); }
          uploadNext(i+1);
        }).catch(function() { uploadNext(i+1); });
      };
      uploadNext(0);
    }
  }

  var _rptUploadedMedia = '';
  var _rptCarouselUrls = [];

  function resetRptPreview() {
    document.getElementById('rpt-upload-preview').style.display = 'none';
    document.getElementById('rpt-upload-placeholder').style.display = '';
    document.getElementById('rpt-carousel-thumbs').innerHTML = '';
    document.getElementById('rpt-preview-video').style.display = 'none';
    document.getElementById('rpt-preview-video').src = '';
    document.getElementById('rpt-preview-filename').textContent = '';
    document.getElementById('rpt-img-count').textContent = '0';
    _rptUploadedMedia = '';
    _rptCarouselUrls = [];
  }

  function handleRptMediaUpload(files) {
    if (!files || files.length === 0) return;
    var statusEl = document.getElementById('rpt-status');
    var isVideo = files[0].type.startsWith('video/');
    _rptUploadedMedia = '';
    _rptCarouselUrls = [];
    if (isVideo) {
      var fd = new FormData();
      fd.append('media', files[0]);
      statusEl.innerHTML = _t('uploading_video');
      fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) {
          _rptUploadedMedia = d.url;
          document.getElementById('rpt-upload-placeholder').style.display = 'none';
          document.getElementById('rpt-upload-preview').style.display = '';
          document.getElementById('rpt-preview-video').src = d.url;
          document.getElementById('rpt-preview-video').style.display = 'block';
          document.getElementById('rpt-preview-filename').textContent = files[0].name;
          statusEl.innerHTML = _t('video_uploaded');
        } else { statusEl.innerHTML = _t('upload_failed'); }
      }).catch(function() { statusEl.innerHTML = _t('upload_error'); });
    } else {
      var uploadNext = function(i) {
        if (i >= files.length) {
          document.getElementById('rpt-upload-placeholder').style.display = 'none';
          document.getElementById('rpt-upload-preview').style.display = '';
          document.getElementById('rpt-img-count').textContent = files.length;
          document.getElementById('rpt-preview-filename').textContent = files.length + ' ' + _t('selected');
          var thumbs = document.getElementById('rpt-carousel-thumbs');
          thumbs.innerHTML = _rptCarouselUrls.map(function(u) {
            return '<div style="position:relative;display:inline-block;"><img src="' + u + '" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:2px solid #ddd;"></div>';
          }).join('');
          statusEl.innerHTML = _rptCarouselUrls.length + ' ' + _t('images_uploaded');
          return;
        }
        var fd = new FormData();
        fd.append('media', files[i]);
        statusEl.innerHTML = _t('uploading') + ' ' + (i+1) + '/' + files.length;
        fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
          if (d.success) { _rptCarouselUrls.push(d.url); }
          uploadNext(i+1);
        }).catch(function() { uploadNext(i+1); });
      };
      uploadNext(0);
    }
  }

  function generateCpCopy() {
    var instruction = document.getElementById('cp-ai-instruction').value.trim() || 'promote a business';
    document.getElementById('cp-status').innerHTML = _t('generating');
    fetch('/api/generate-ad-copy', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instruction: instruction, count: 3})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.headlines && d.headlines.length) {
          document.getElementById('cp-headline').value = d.headlines[0] || '';
          document.getElementById('cp-message').value = d.messages ? d.messages.join('\\n\\n') : '';
          document.getElementById('cp-status').innerHTML = _t('ad_copy_generated');
          setTimeout(function() { document.getElementById('cp-status').innerHTML = ''; }, 3000);
        } else { document.getElementById('cp-status').innerHTML = _t('generation_failed'); }
      }).catch(function() { document.getElementById('cp-status').innerHTML = _t('error'); });
  }

  function getCpPlatforms() {
    var platforms = [];
    if (document.getElementById('cp-platform-fb') && document.getElementById('cp-platform-fb').checked) platforms.push('facebook');
    if (document.getElementById('cp-platform-ig') && document.getElementById('cp-platform-ig').checked) platforms.push('instagram');
    return platforms;
  }

  function getCpPayload() {
    var isCarousel = _cpCarouselUrls.length > 0;
    var isVideo = !isCarousel && _cpUploadedMedia && document.getElementById('cp-preview-video').style.display !== 'none' && document.getElementById('cp-preview-video').style.display !== '';
    var platforms = getCpPlatforms();
    var payload = {
      platform: platforms[0] || 'facebook',
      platforms: platforms,
      headline: document.getElementById('cp-headline').value.trim(),
      message: document.getElementById('cp-message').value.trim(),
      ai_instruction: document.getElementById('cp-ai-instruction').value.trim(),
      cta: document.getElementById('cp-cta').value,
      link_url: document.getElementById('cp-link').value.trim(),
      content_type: isVideo ? 'video' : (isCarousel ? 'carousel' : 'image'),
      media_url: isCarousel ? '' : _cpUploadedMedia,
      media_file: isCarousel ? '' : _cpUploadedMedia,
      media_urls: _cpCarouselUrls,
      media_files: _cpCarouselUrls
    };
    return payload;
  }

  function savePostAsDraft() {
    var payload = getCpPayload();
    payload.status = 'draft';
    payload.scheduled_time = null;
    submitPost(payload, _t('draft_saved'));
  }

  function getCpScheduleValue() {
    var date = document.getElementById('cp-schedule-start-date').value;
    var hour = document.getElementById('cp-schedule-hour').value;
    var min = document.getElementById('cp-schedule-min').value;
    var ampm = document.getElementById('cp-schedule-ampm').value;
    if (!date || !hour || !min) return '';
    var h = parseInt(hour, 10);
    var h24 = (ampm === 'PM' && h !== 12) ? h + 12 : (ampm === 'AM' && h === 12 ? 0 : h);
    return date + 'T' + String(h24).padStart(2, '0') + ':' + min;
  }

  function addCpScheduleManualTime() {
    var sched = getCpScheduleValue();
    if (!sched) { showToast(_t('select_schedule')); return; }
    document.getElementById('cp-schedule').value = sched;
    var dt = new Date(sched);
    var display = dt.toLocaleDateString() + ' ' + dt.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    document.getElementById('cp-schedule-confirm-text').textContent = (_t('time_added') || 'Time added') + ': ' + display;
    document.getElementById('cp-schedule-confirm').style.display = 'flex';
    document.getElementById('cp-schedule-manual-empty').style.display = 'none';
  }

  function clearCpScheduleManualTime() {
    document.getElementById('cp-schedule').value = '';
    document.getElementById('cp-schedule-confirm').style.display = 'none';
    document.getElementById('cp-schedule-manual-empty').style.display = '';
  }

  function sendCpToRepeat(totalDays) {
    var payload = getCpPayload();
    if (!payload.message && !payload.media_url && !(payload.media_urls && payload.media_urls.length)) {
      showToast(_t('add_content_first'));
      return;
    }
    payload.status = 'draft';
    payload.scheduled_time = null;
    var statusEl = document.getElementById('cp-status');
    statusEl.innerHTML = _t('processing');
    fetch('/api/posts/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.success && d.post && d.post.id) {
          closeCreatePostModal();
          openRepeatModal(d.post.id, function() {
            var rptStartDate = document.getElementById('rpt-start-date');
            if (rptStartDate && !rptStartDate.value) {
              rptStartDate.value = new Date().toISOString().slice(0, 10);
            }
            _cpRepeatPendingDays = totalDays;
            showToast(_t('repeat_setup_hint'));
          });
        } else {
          statusEl.innerHTML = _t('error') + ': ' + (d.error || 'Unknown');
        }
      }).catch(function(e) { statusEl.innerHTML = 'Error: ' + e.message; });
  }

  function schedulePost() {
    var sched = document.getElementById('cp-schedule').value || getCpScheduleValue();
    if (!sched) { document.getElementById('cp-status').innerHTML = _t('select_schedule'); return; }
    var payload = getCpPayload();
    payload.status = 'scheduled';
    payload.scheduled_time = new Date(sched).getTime() / 1000;
    submitPost(payload, _t('post_scheduled'));
  }

  function publishPostNow() {
    var payload = getCpPayload();
    payload.status = 'publish_now';
    payload.scheduled_time = null;
    submitPost(payload, _t('publishing'));
  }

  function submitPost(payload, successMsg) {
    var platforms = payload.platforms && payload.platforms.length ? payload.platforms : [payload.platform || 'facebook'];

    // Validate at least one platform selected
    var warn = document.getElementById('cp-platform-warning');
    if (!platforms.length) {
      if (warn) warn.style.display = 'block';
      return;
    }
    if (warn) warn.style.display = 'none';

    var statusEl = document.getElementById('cp-status');
    statusEl.innerHTML = _t('processing');

    // If only one platform, send as before
    if (platforms.length === 1) {
      payload.platform = platforms[0];
      fetch('/api/posts/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d.success) {
            statusEl.innerHTML = successMsg;
            if (d.published) showToast(_t('post_published'));
            setTimeout(function() { closeCreatePostModal(); loadPosts(); }, 1000);
          } else {
            statusEl.innerHTML = _t('error') + ': ' + (d.error || 'Unknown');
          }
        }).catch(function(e) { statusEl.innerHTML = 'Error: ' + e.message; });
      return;
    }

    // Both platforms — send one request per platform in sequence
    var results = [];
    var errors = [];
    statusEl.innerHTML = '⏳ Posting to ' + platforms.join(' & ') + '...';

    function postNext(index) {
      if (index >= platforms.length) {
        // All done
        if (errors.length === 0) {
          statusEl.innerHTML = '✅ Posted to ' + platforms.join(' & ') + '!';
          showToast('✅ Published to Facebook & Instagram!');
        } else if (results.length > 0) {
          statusEl.innerHTML = '⚠️ Partial: ' + results.join(', ') + ' OK. Errors: ' + errors.join(', ');
        } else {
          statusEl.innerHTML = _t('error') + ': ' + errors.join(', ');
        }
        setTimeout(function() { closeCreatePostModal(); loadPosts(); }, 1500);
        return;
      }
      var pl = platforms[index];
      var p2 = JSON.parse(JSON.stringify(payload));
      p2.platform = pl;
      p2.platforms = [pl];
      fetch('/api/posts/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(p2)})
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d.success) { results.push(pl); }
          else { errors.push(pl + ': ' + (d.error || 'error')); }
          postNext(index + 1);
        }).catch(function(e) {
          errors.push(pl + ': ' + e.message);
          postNext(index + 1);
        });
    }
    postNext(0);
  }  function publishPost(postId) {
    if (!confirm(_t('confirm_publish_post'))) return;
    fetch('/api/posts/publish/' + postId, {method:'POST'})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.success) { showToast(_t('post_published')); loadPosts(); }
        else { showToast(_t('error') + ': ' + (d.error || '')); }
      }).catch(function() {});
  }

  function deletePost(postId) {
    if (!confirm(_t('confirm_trash'))) return;
    fetch('/api/posts/delete/' + postId, {method:'POST'})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.success) { loadPosts(); showToast(_t('moved_to_trash')); }
        else { showToast(_t('error') + ': ' + _t('moved_to_trash')); }
      }).catch(function() {});
  }

  function restorePost(postId) {
    fetch('/api/posts/restore/' + postId, {method:'POST'})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.success) { loadPosts(); showToast(_t('post_restored')); }
        else { showToast(_t('error') + ': ' + _t('post_restored')); }
      }).catch(function() {});
  }

  function deleteForever(postId) {
    if (!confirm(_t('confirm_delete_forever'))) return;
    fetch('/api/posts/delete-forever/' + postId, {method:'POST'})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.success) { loadPosts(); showToast(_t('post_deleted_permanent')); }
        else { showToast(_t('error') + ': ' + _t('post_deleted_permanent')); }
      }).catch(function() {});
  }

  function showPostDetail(postId) {
    fetch('/api/posts').then(function(r) { return r.json(); }).then(function(d) {
      var posts = d.posts || [];
      var p = posts.find(function(x) { return x.id === postId; });
      if (!p) return;

      function fmtDate(dt) {
        if (!dt) return '-';
        var d2 = new Date(dt);
        return d2.toLocaleDateString() + ' ' + d2.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
      }
      var statuses = {published:_t('published'),scheduled:_t('scheduled'),draft:_t('drafts'),trashed:_t('trash')};

      // Overlay
      var overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';

      // Box
      var box = document.createElement('div');
      box.style.cssText = 'background:#fff;border-radius:12px;max-width:620px;width:90%;max-height:85vh;overflow-y:auto;padding:24px;box-shadow:0 8px 32px rgba(0,0,0,0.2);';

      // Header
      var hdr = document.createElement('div');
      hdr.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;';
      var title = document.createElement('h3');
      title.style.cssText = 'margin:0;font-size:18px;';
      title.textContent = '📄 ' + _t('details');
      var closeBtn = document.createElement('button');
      closeBtn.style.cssText = 'background:none;border:none;font-size:24px;cursor:pointer;color:#65676b;';
      closeBtn.textContent = '×';
      closeBtn.onclick = function() { overlay.remove(); };
      hdr.appendChild(title);
      hdr.appendChild(closeBtn);
      box.appendChild(hdr);

      // Media preview
      var imgSrc = p.media_file || p.media_url || '';
      var allImgs = (p.media_files && p.media_files.length) ? p.media_files : ((p.media_urls && p.media_urls.length) ? p.media_urls : []);

      if (allImgs.length > 1) {
        var label = document.createElement('div');
        label.style.cssText = 'margin-top:16px;margin-bottom:8px;';
        label.innerHTML = '<strong>📸 Carousel (' + allImgs.length + ' ' + _t('image') + 's):</strong>';
        box.appendChild(label);
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';
        allImgs.forEach(function(src) {
          var img = document.createElement('img');
          img.src = src;
          img.style.cssText = 'max-width:160px;max-height:140px;border-radius:8px;object-fit:cover;border:1px solid #e4e6eb;';
          img.onerror = function() { this.style.display = 'none'; };
          row.appendChild(img);
        });
        box.appendChild(row);
      } else if (imgSrc) {
        var isVideo = /\\.(mp4|mov|avi|webm)$/i.test(imgSrc);
        var mediaLabel = document.createElement('div');
        mediaLabel.style.cssText = 'margin-top:16px;margin-bottom:8px;';
        mediaLabel.innerHTML = '<strong>' + (isVideo ? '🎥 Video' : '📸 ' + _t('image')) + ':</strong>';
        box.appendChild(mediaLabel);
        if (isVideo) {
          var vid = document.createElement('video');
          vid.src = imgSrc;
          vid.controls = true;
          vid.style.cssText = 'max-width:100%;max-height:280px;border-radius:8px;display:block;';
          box.appendChild(vid);
        } else {
          var img = document.createElement('img');
          img.src = imgSrc;
          img.style.cssText = 'max-width:100%;max-height:280px;border-radius:8px;object-fit:contain;border:1px solid #e4e6eb;display:block;';
          img.onerror = function() {
            var warn = document.createElement('div');
            warn.style.cssText = 'color:#65676b;padding:12px;background:#f0f2f5;border-radius:8px;';
            warn.textContent = '⚠️ Image not available';
            img.parentNode.replaceChild(warn, img);
          };
          box.appendChild(img);
        }
      }

      // Info table
      var tableWrap = document.createElement('div');
      tableWrap.style.marginTop = '16px';
      var rows = [
        [_t('platform'), p.platform === 'facebook' ? _t('facebook') : _t('instagram')],
        [_t('type'), p.content_type === 'image' ? _t('image') : p.content_type === 'video' ? _t('video') : p.content_type === 'carousel' ? _t('carousel') : (p.content_type || '-')],
        [_t('status'), statuses[p.status] || p.status],
      ];
      if (p.published_at) rows.push([_t('published'), fmtDate(p.published_at)]);
      if (p.scheduled_time) rows.push([_t('scheduled'), fmtDate(p.scheduled_time)]);
      rows.push([_t('created'), fmtDate(p.created_at)]);

      var tbl = document.createElement('table');
      tbl.style.cssText = 'width:100%;border-collapse:collapse;';
      rows.forEach(function(r) {
        var tr = document.createElement('tr');
        var td1 = document.createElement('td');
        td1.style.cssText = 'padding:8px 12px;font-weight:700;color:#65676b;border-bottom:1px solid #e4e6eb;width:120px;';
        td1.textContent = r[0];
        var td2 = document.createElement('td');
        td2.style.cssText = 'padding:8px 12px;border-bottom:1px solid #e4e6eb;';
        td2.textContent = r[1];
        tr.appendChild(td1);
        tr.appendChild(td2);
        tbl.appendChild(tr);
      });
      tableWrap.appendChild(tbl);
      box.appendChild(tableWrap);

      // Message
      var msgLabel = document.createElement('div');
      msgLabel.style.cssText = 'margin-top:16px;margin-bottom:8px;';
      msgLabel.innerHTML = '<strong>' + _t('message') + ':</strong>';
      var msgBox = document.createElement('div');
      msgBox.style.cssText = 'background:#f0f2f5;border-radius:8px;padding:12px;white-space:pre-wrap;font-size:14px;line-height:1.5;';
      msgBox.textContent = p.message || ('(' + _t('no_message') + ')');
      box.appendChild(msgLabel);
      box.appendChild(msgBox);

      overlay.appendChild(box);
      document.body.appendChild(overlay);
      overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    }).catch(function() {});
  }
  // ================================================================
  // LEAD MANAGEMENT FUNCTIONS
  // ================================================================
  function loadLeads() {
    var statusEl = document.getElementById('lead-status-filter');
    var searchEl = document.getElementById('lead-search');
    var status = statusEl ? statusEl.value : '';
    var search = searchEl ? searchEl.value.toLowerCase() : '';
    var url = '/api/leads-v2?limit=500' + (status ? '&status=' + status : '');
    fetch(url).then(function(r) { return r.json(); }).then(function(d) {
      var leads = d.leads || [];
      if (search) {
        leads = leads.filter(function(l) {
          return (l.name || '').toLowerCase().indexOf(search) > -1 ||
                 (l.email || '').toLowerCase().indexOf(search) > -1 ||
                 (l.phone || '').indexOf(search) > -1;
        });
      }
      renderLeadsTable(leads);
      loadLeadStats();
    }).catch(function() {});
  }

  function renderLeadsTable(leads) {
    var tbody = document.getElementById('leads-table');
    if (!leads.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#65676b;padding:30px;">' + _t('no_leads') + '</td></tr>';
      return;
    }
    var html = '';
    leads.forEach(function(l) {
      var statusClass = l.status === 'converted' ? 'badge badge-success' : l.status === 'trial_booked' ? 'badge badge-warning' : l.status === 'contacted' ? 'badge badge-info' : l.status === 'lost' ? 'badge badge-danger' : 'badge';
      html += '<tr>' +
        '<td>' + _esc(l.name || '-') + '</td>' +
        '<td>' + _esc(l.email || '-') + '</td>' +
        '<td>' + _esc(l.phone || '-') + '</td>' +
        '<td><strong>' + (l.score || 0) + '</strong></td>' +
        '<td><span class="' + statusClass + '">' + _t(l.status || 'new') + '</span></td>' +
        '<td>' + _esc(l.source || 'meta') + '</td>' +
        '<td>' +
        '<button class="btn btn-sm btn-primary" onclick="updateLeadStatus(\\'' + l.lead_id + '\\',\\'contacted\\')" title="' + _t('contacted') + '">' + _t('contacted') + '</button> ' +
        '<button class="btn btn-sm btn-success" onclick="updateLeadStatus(\\'' + l.lead_id + '\\',\\'converted\\')" title="' + _t('converted') + '">' + _t('converted') + '</button> ' +
        '<button class="btn btn-sm" style="background:#e4e6eb;" onclick="scoreLead(\\'' + l.lead_id + '\\')" title="' + _t('score') + '">' + _t('score') + '</button> ' +
        '<button class="btn btn-sm btn-danger" onclick="deleteLead(\\'' + l.lead_id + '\\')" title="' + _t('delete') + '">' + _t('delete') + '</button>' +
        '</td></tr>';
    });
    tbody.innerHTML = html;
  }

  function loadLeadStats() {
    fetch('/api/leads-v2/stats').then(function(r) { return r.json(); }).then(function(d) {
      if (d.total !== undefined) document.getElementById('lead-stat-total').textContent = d.total;
      if (d.by_status) {
        document.getElementById('lead-stat-new').textContent = d.by_status.new || 0;
        document.getElementById('lead-stat-contacted').textContent = d.by_status.contacted || 0;
        document.getElementById('lead-stat-converted').textContent = d.by_status.converted || 0;
      }
      if (d.average_score !== undefined) document.getElementById('lead-stat-avg-score').textContent = d.average_score;
    }).catch(function() {});
  }

  function fetchMetaLeads() {
    showToast(_t('fetching_leads'));
    fetch('/api/leads-v2/fetch-meta', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast('Fetched ' + d.leads_count + ' leads!'); loadLeads(); }
        else { showToast('Error: ' + (d.error || '')); }
      }).catch(function() {});
  }

  function updateLeadStatus(leadId, status) {
    fetch('/api/leads-v2/status/' + leadId, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status:status})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast('Lead ' + status + '!'); loadLeads(); }
      }).catch(function() {});
  }

  function scoreLead(leadId) {
    fetch('/api/leads-v2/score/' + leadId, {method:'POST'})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('score') + ': ' + d.score); loadLeads(); }
      }).catch(function() {});
  }

  function deleteLead(leadId) {
    if (!confirm('Delete this lead?')) return;
    fetch('/api/leads-v2/delete/' + leadId, {method:'POST'})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('lead_deleted')); loadLeads(); }
      }).catch(function() {});
  }

  function openAddLeadModal() {
    document.getElementById('add-lead-modal').classList.add('open');
  }

  function closeAddLeadModal() {
    document.getElementById('add-lead-modal').classList.remove('open');
  }

  function saveLead() {
    var name = document.getElementById('lead-name').value;
    var email = document.getElementById('lead-email').value;
    var phone = document.getElementById('lead-phone').value;
    var notes = document.getElementById('lead-notes').value;
    if (!name) { showToast(_t('name_required')); return; }
    fetch('/api/leads-v2/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name, email:email, phone:phone, notes:notes})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('lead_added')); closeAddLeadModal(); loadLeads(); }
        else { showToast('Error: ' + (d.error || '')); }
      }).catch(function() {});
  }

  function loadWorkflows() {
    fetch('/api/leads-v2/workflows').then(function(r) { return r.json(); }).then(function(d) {
      var wfs = d.workflows || [];
      var container = document.getElementById('workflows-list');
      if (!wfs.length) {
        container.innerHTML = '<p style="color:#65676b;">' + _t('no_leads') + '</p>';
        return;
      }
      var html = '';
      wfs.forEach(function(w) {
        html += '<div style="padding:10px 14px;background:#f0f2f5;border-radius:8px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">' +
          '<div><strong>' + _esc(w.name) + '</strong> <span style="font-size:12px;color:#65676b;">(' + (w.steps || []).length + ' steps)</span></div>' +
          '<button class="btn btn-sm btn-danger" onclick="deleteWorkflow(\\'' + w.id + '\\')">' + _t('delete') + '</button></div>';
      });
      container.innerHTML = html;
    }).catch(function() {});
  }

  function openCreateWorkflowModal() {
    document.getElementById('create-workflow-modal').classList.add('open');
  }

  function closeCreateWorkflowModal() {
    document.getElementById('create-workflow-modal').classList.remove('open');
  }

  function saveWorkflow() {
    var name = document.getElementById('wf-name').value;
    var stepsStr = document.getElementById('wf-steps').value;
    var steps = [];
    try { steps = JSON.parse(stepsStr); } catch(e) { showToast(_t('invalid_json_steps')); return; }
    if (!name) { showToast(_t('name_required')); return; }
    fetch('/api/leads-v2/workflows/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name, steps:steps})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('workflow_created')); closeCreateWorkflowModal(); loadWorkflows(); }
      }).catch(function() {});
  }

  function deleteWorkflow(wfId) {
    if (!confirm('Delete this workflow?')) return;
    fetch('/api/leads-v2/workflows/delete/' + wfId, {method:'POST'})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('workflow_deleted')); loadWorkflows(); }
      }).catch(function() {});
  }

  // ================================================================
  // MULTI-PLATFORM SCHEDULER FUNCTIONS
  // ================================================================
  function loadMultiQueue() {
    fetch('/api/multi-scheduler/queue').then(function(r) { return r.json(); }).then(function(d) {
      var items = d.items || [];
      var tbody = document.getElementById('multi-queue-table');
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#65676b;padding:30px;">' + _t('no_scheduled_posts') + '</td></tr>';
        return;
      }
      var html = '';
      items.forEach(function(item) {
        var platforms = (item.platforms || []).join(', ');
        var sched = item.scheduled_time ? new Date(item.scheduled_time).toLocaleString() : 'Now';
        var statusClass = item.status === 'published' ? 'badge badge-success' : item.status === 'partial' ? 'badge badge-warning' : 'badge';
        var ctype = item.content_type || 'image';
        var hasMedia = (item.media_file || item.media_files || []).length > 0;
        html += '<tr>' +
          '<td>' + _esc(platforms) + '</td>' +
          '<td>' + _esc((item.message || '').substring(0, 50)) + '</td>' +
          '<td>' + (ctype === 'video' ? _t('video') : ctype === 'carousel' ? _t('carousel') : _t('image')) + (hasMedia ? ' 📎' : '') + '</td>' +
          '<td>' + sched + '</td>' +
          '<td><span class="' + statusClass + '">' + _esc(item.status) + '</span></td>' +
          '<td>' +
          (item.status === 'pending' ? '<button class="btn btn-sm btn-primary" onclick="publishMultiItem(\\'' + item.id + '\\')">' + _t('publish') + '</button> ' : '') +
          (item.results && item.results.facebook ? '<span style="font-size:11px;color:#65676b;">FB:' + (item.results.facebook.error || 'OK') + '</span> ' : '') +
          (item.results && item.results.instagram ? '<span style="font-size:11px;color:#65676b;">IG:' + (item.results.instagram.error || 'OK') + '</span>' : '') +
          '<button class="btn btn-sm btn-primary" onclick="openMultiRepeatModal(\\'' + item.id + '\\')" style="margin-left:4px;" title="' + _t('repeat') + '">🔁</button>' +
          '<button class="btn btn-sm btn-danger" onclick="deleteMultiItem(\\'' + item.id + '\\')" style="margin-left:4px;">' + _t('delete') + '</button>' +
          '</td></tr>';
      });
      tbody.innerHTML = html;
    }).catch(function() {});
  }

  function publishMultiItem(itemId) {
    fetch('/api/multi-scheduler/publish/' + itemId, {method:'POST'})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('published')); loadMultiQueue(); }
        else { showToast(_t('error')); }
      }).catch(function() {});
  }

  function deleteMultiItem(itemId) {
    if (!confirm('Delete this scheduled post?')) return;
    fetch('/api/multi-scheduler/delete/' + itemId, {method:'POST'})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('deleted')); loadMultiQueue(); }
      }).catch(function() {});
  }

  var _mpUploadedMedia = '';
  var _mpCarouselUrls = [];

  function resetMpModal() {
    document.getElementById('mp-headline').value = '';
    document.getElementById('mp-message').value = '';
    document.getElementById('mp-ai-instruction').value = '';
    document.getElementById('mp-cta').value = 'LEARN_MORE';
    document.getElementById('mp-link').value = '';
    document.getElementById('mp-schedule-date').value = '';
    document.getElementById('mp-schedule-hour').value = '';
    document.getElementById('mp-schedule-min').value = '';
    document.getElementById('mp-schedule-ampm').value = 'AM';
    document.getElementById('mp-schedule').value = '';
    document.getElementById('mp-status').innerHTML = '';
    resetMpPreview();
  }

  function openMultiScheduleModal() {
    try {
      resetMpModal();
      document.getElementById('multi-schedule-modal').classList.add('open');
    } catch(e) {
      console.error('Error in openMultiScheduleModal:', e);
      document.getElementById('multi-schedule-modal').classList.add('open');
    }
  }
  
    function closeMultiScheduleModal() {
      document.getElementById('multi-schedule-modal').classList.remove('open');
    }

  function resetMpPreview() {
    document.getElementById('mp-upload-preview').style.display = 'none';
    document.getElementById('mp-upload-placeholder').style.display = '';
    document.getElementById('mp-carousel-thumbs').innerHTML = '';
    document.getElementById('mp-preview-video').style.display = 'none';
    document.getElementById('mp-preview-video').src = '';
    document.getElementById('mp-preview-filename').textContent = '';
    document.getElementById('mp-img-count').textContent = '0';
    _mpUploadedMedia = '';
    _mpCarouselUrls = [];
  }

  // mp-media-input onchange is handled inline via HTML attribute

  function handleMpMediaUpload(files) {
    if (!files || files.length === 0) return;
    var statusEl = document.getElementById('mp-status');
    var isVideo = files[0].type.startsWith('video/');
    _mpUploadedMedia = '';
    _mpCarouselUrls = [];
    if (isVideo) {
      var fd = new FormData();
      fd.append('media', files[0]);
      statusEl.innerHTML = _t('uploading_video');
      fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) {
          _mpUploadedMedia = d.url;
          document.getElementById('mp-upload-placeholder').style.display = 'none';
          document.getElementById('mp-upload-preview').style.display = '';
          document.getElementById('mp-preview-video').src = d.url;
          document.getElementById('mp-preview-video').style.display = 'block';
          document.getElementById('mp-preview-filename').textContent = files[0].name;
          statusEl.innerHTML = _t('video_uploaded');
        } else { statusEl.innerHTML = _t('upload_failed'); }
      }).catch(function() { statusEl.innerHTML = _t('upload_error'); });
    } else {
      var uploadNext = function(i) {
        if (i >= files.length) {
          document.getElementById('mp-upload-placeholder').style.display = 'none';
          document.getElementById('mp-upload-preview').style.display = '';
          document.getElementById('mp-img-count').textContent = files.length;
          document.getElementById('mp-preview-filename').textContent = files.length + ' ' + _t('selected');
          var thumbs = document.getElementById('mp-carousel-thumbs');
          thumbs.innerHTML = _mpCarouselUrls.map(function(u) {
            return '<div style="position:relative;display:inline-block;"><img src="' + u + '" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:2px solid #ddd;"></div>';
          }).join('');
          statusEl.innerHTML = _mpCarouselUrls.length + ' ' + _t('images_uploaded');
          return;
        }
        var fd = new FormData();
        fd.append('media', files[i]);
        statusEl.innerHTML = _t('uploading') + ' ' + (i+1) + '/' + files.length;
        fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
          if (d.success) { _mpCarouselUrls.push(d.url); }
          uploadNext(i+1);
        }).catch(function() { uploadNext(i+1); });
      };
      uploadNext(0);
    }
  }

  function generateMpCopy() {
    var instruction = document.getElementById('mp-ai-instruction').value.trim() || 'promote a business';
    document.getElementById('mp-status').innerHTML = _t('generating');
    fetch('/api/generate-ad-copy', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instruction: instruction, count: 3})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.headlines && d.headlines.length) {
          document.getElementById('mp-headline').value = d.headlines[0] || '';
          document.getElementById('mp-message').value = d.messages ? d.messages.join('\\n\\n') : '';
          document.getElementById('mp-status').innerHTML = _t('ad_copy_generated');
          setTimeout(function() { document.getElementById('mp-status').innerHTML = ''; }, 3000);
        } else { document.getElementById('mp-status').innerHTML = _t('generation_failed'); }
      }).catch(function() { document.getElementById('mp-status').innerHTML = _t('error'); });
  }

  function generateRptCopy() {
    var instruction = document.getElementById('rpt-ai-instruction').value.trim() || 'promote a business';
    document.getElementById('rpt-status').innerHTML = _t('generating');
    fetch('/api/generate-ad-copy', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instruction: instruction, count: 3})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.headlines && d.headlines.length) {
          document.getElementById('rpt-headline').value = d.headlines[0] || '';
          document.getElementById('rpt-message').value = d.messages ? d.messages.join('\\n\\n') : '';
          document.getElementById('rpt-status').innerHTML = _t('ad_copy_generated');
          setTimeout(function() { document.getElementById('rpt-status').innerHTML = ''; }, 3000);
        } else { document.getElementById('rpt-status').innerHTML = _t('generation_failed'); }
      }).catch(function() { document.getElementById('rpt-status').innerHTML = _t('error'); });
  }

  function submitMultiPost(statusOverride) {
    var platforms = [];
    document.querySelectorAll('.mp-platform:checked').forEach(function(cb) { platforms.push(cb.value); });
    if (!platforms.length) { showToast(_t('select_platform')); return; }
    var headline = document.getElementById('mp-headline').value.trim();
    var message = document.getElementById('mp-message').value.trim();
    var aiInstruction = document.getElementById('mp-ai-instruction').value.trim();
    var cta = document.getElementById('mp-cta').value;
    var linkUrl = document.getElementById('mp-link').value.trim();
    var schedule = document.getElementById('mp-schedule').value;
    var isCarousel = _mpCarouselUrls.length > 0;
    var isVideo = !isCarousel && _mpUploadedMedia && document.getElementById('mp-preview-video').style.display !== 'none' && document.getElementById('mp-preview-video').style.display !== '';
    var payload = {
      platforms: platforms,
      headline: headline,
      message: message,
      ai_instruction: aiInstruction,
      cta: cta,
      link_url: linkUrl,
      content_type: isVideo ? 'video' : (isCarousel ? 'carousel' : 'image'),
      media_file: isCarousel ? '' : _mpUploadedMedia,
      media_files: _mpCarouselUrls,
      scheduled_time: schedule || null
    };
    if (statusOverride === 'draft') {
      payload.scheduled_time = null;
    }
    var statusEl = document.getElementById('mp-status');
    statusEl.innerHTML = _t('saving');
    var url = '/api/multi-scheduler/schedule';
    fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) {
          statusEl.innerHTML = _t('saved');
          showToast(_t('post_scheduled'));
          setTimeout(function() { closeMultiScheduleModal(); loadMultiQueue(); }, 800);
        } else { statusEl.innerHTML = _t('error') + ': ' + (d.error || ''); }
      }).catch(function(e) { statusEl.innerHTML = 'Error: ' + e.message; });
  }

  function saveMultiDraft() { submitMultiPost('draft'); }
  function saveMultiSchedule() { submitMultiPost(null); }

  function publishMultiNow() {
    var scheduleEl = document.getElementById('mp-schedule');
    scheduleEl.value = '';
    submitMultiPost(null);
  }  // AUTO RESPONDER FUNCTIONS
  // ================================================================
  function loadResponder() {
    fetch('/api/responder/status').then(function(r) { return r.json(); }).then(function(d) {
      document.getElementById('responder-enabled').checked = d.enabled;
      document.getElementById('responder-use-ai').checked = d.use_ai;
      document.getElementById('responder-default').value = d.rules.default_response || '';
    }).catch(function() {});
    loadResponderRules();
    loadResponderLog();
  }

  function loadResponderRules() {
    fetch('/api/responder/rules').then(function(r) { return r.json(); }).then(function(d) {
      var rules = d.rules || [];
      var container = document.getElementById('responder-rules');
      if (!rules.length) {
        container.innerHTML = '<p style="color:#65676b;">' + _t('no_leads') + '</p>';
        return;
      }
      var html = '';
      rules.forEach(function(rule) {
        html += '<div style="padding:10px 14px;background:#f0f2f5;border-radius:8px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">' +
          '<div><strong>' + _esc(rule.keyword) + '</strong> &rarr; ' + _esc(rule.response_template) + ' <span style="font-size:12px;color:#65676b;">(' + rule.platform + ')</span></div>' +
          '<button class="btn btn-sm btn-danger" onclick="deleteRule(\\'' + rule.id + '\\')">' + _t('delete') + '</button></div>';
      });
      container.innerHTML = html;
    }).catch(function() {});
  }

  function loadResponderLog() {
    fetch('/api/responder/log?limit=20').then(function(r) { return r.json(); }).then(function(d) {
      var logs = d.responses || [];
      var tbody = document.getElementById('responder-log-table');
      if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#65676b;padding:30px;">' + _t('no_responses') + '</td></tr>';
        return;
      }
      var html = '';
      logs.forEach(function(log) {
        var statusClass = log.success ? 'badge badge-success' : 'badge badge-danger';
        html += '<tr>' +
          '<td>' + (log.timestamp || '-') + '</td>' +
          '<td>' + _esc(log.from_name || '-') + '</td>' +
          '<td>' + _esc((log.original_message || '').substring(0, 50)) + '</td>' +
          '<td>' + _esc((log.response || '').substring(0, 50)) + '</td>' +
          '<td><span class="' + statusClass + '">' + (log.success ? 'OK' : 'FAIL') + '</span></td></tr>';
      });
      tbody.innerHTML = html;
    }).catch(function() {});
  }

  function toggleResponder() {
    var enabled = document.getElementById('responder-enabled').checked;
    fetch('/api/responder/toggle', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:enabled})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) showToast(enabled ? 'Responder enabled' : 'Responder disabled');
      }).catch(function() {});
  }

  function toggleResponderAI() {
    var enabled = document.getElementById('responder-use-ai').checked;
    fetch('/api/responder/ai-mode', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:enabled})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) showToast('AI mode ' + (enabled ? 'on' : 'off'));
      }).catch(function() {});
  }

  function saveDefaultResponse() {
    var text = document.getElementById('responder-default').value;
    fetch('/api/responder/default-response', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) showToast(_t('default_response_saved'));
      }).catch(function() {});
  }

  function openAddRuleModal() {
    document.getElementById('add-rule-modal').classList.add('open');
  }

  function closeAddRuleModal() {
    document.getElementById('add-rule-modal').classList.remove('open');
  }

  function saveRule() {
    var keyword = document.getElementById('rule-keyword').value;
    var response = document.getElementById('rule-response').value;
    var platform = document.getElementById('rule-platform').value;
    if (!keyword || !response) { showToast(_t('keyword_response_required')); return; }
    fetch('/api/responder/rules/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({keyword:keyword, response_template:response, platform:platform})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('rule_added')); closeAddRuleModal(); loadResponderRules(); }
      }).catch(function() {});
  }

  function deleteRule(ruleId) {
    if (!confirm('Delete this rule?')) return;
    fetch('/api/responder/rules/delete/' + ruleId, {method:'POST'})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast(_t('rule_deleted')); loadResponderRules(); }
      }).catch(function() {});
  }

  function scanComments() {
    showToast(_t('scanning_comments'));
    fetch('/api/responder/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({limit:20})})
      .then(function(r) { return r.json(); }).then(function(d) {
        if (d.success) { showToast('Responded to ' + (d.results || []).length + ' comments!'); loadResponderLog(); }
        else { showToast(_t('error_scanning')); }
      }).catch(function() {});
  }

  // ================================================================

  // Inject Meta Pixel if configured
  fetch('/api/pixel').then(function(r) { return r.json(); }).then(function(d) {
    if (d.pixel_id) {
      var pixelId = d.pixel_id;
      var img = new Image();
      img.src = 'https://www.facebook.com/tr?id=' + pixelId + '&ev=PageView&noscript=1';
      img.style.display = 'none';
      document.body.appendChild(img);
      var script = document.createElement('script');
      script.innerHTML = "!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');fbq('init','" + pixelId + "');fbq('track','PageView');";
      document.head.appendChild(script);
    }
  }).catch(function() {});
</script>

<!-- ===== PAGE: LEADS ===== -->
<div class="page" id="page-leads">
  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
      <h2 data-i18n="lead_management">Lead Management</h2>
      <div style="display:flex;gap:8px;">
        <button class="btn btn-primary" onclick="fetchMetaLeads()" data-i18n="fetch_leads">Fetch from Meta</button>
        <button class="btn" onclick="openAddLeadModal()" data-i18n="add_lead">+ Add Lead</button>
        <button class="btn" onclick="loadLeads()" style="background:#e4e6eb;" data-i18n="refresh">Refresh</button>
      </div>
    </div>
  </div>
  <div class="grid" id="lead-stats-row" style="margin-bottom:16px;">
    <div class="stat-card"><div class="num" id="lead-stat-total">0</div><div class="label" data-i18n="total_leads">Total Leads</div></div>
    <div class="stat-card"><div class="num" id="lead-stat-new">0</div><div class="label" data-i18n="new_leads">New</div></div>
    <div class="stat-card"><div class="num" id="lead-stat-contacted">0</div><div class="label" data-i18n="contacted">Contacted</div></div>
    <div class="stat-card"><div class="num" id="lead-stat-converted">0</div><div class="label" data-i18n="converted">Converted</div></div>
    <div class="stat-card"><div class="num" id="lead-stat-avg-score">0</div><div class="label" data-i18n="avg_score">Avg Score</div></div>
  </div>
  <div class="section">
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
      <select id="lead-status-filter" onchange="loadLeads()" style="padding:6px;border:1px solid #ddd;border-radius:6px;">
        <option value="" data-i18n="all">All</option>
        <option value="new" data-i18n="new">New</option>
        <option value="contacted" data-i18n="contacted">Contacted</option>
        <option value="trial_booked" data-i18n="trial_booked">Trial Booked</option>
        <option value="converted" data-i18n="converted">Converted (Enrolled)</option>
        <option value="lost" data-i18n="lost">Lost</option>
      </select>
      <input type="text" id="lead-search" data-i18n-placeholder="search_leads" placeholder="Search leads..." oninput="loadLeads()" style="padding:6px;border:1px solid #ddd;border-radius:6px;flex:1;min-width:150px;">
    </div>
    <table>
      <thead><tr><th data-i18n="name">Name</th><th data-i18n="email">Email</th><th data-i18n="phone">Phone</th><th data-i18n="score">Score</th><th data-i18n="status">Status</th><th data-i18n="source">Source</th><th data-i18n="actions">Actions</th></tr></thead>
      <tbody id="leads-table">
        <tr><td colspan="7" style="text-align:center;color:#65676b;padding:30px;" data-i18n="no_leads">No leads yet.</td></tr>
      </tbody>
    </table>
  </div>
  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <h3 data-i18n="workflows">Workflows</h3>
      <button class="btn btn-sm btn-primary" onclick="openCreateWorkflowModal()" data-i18n="create_workflow">+ Create Workflow</button>
    </div>
    <div id="workflows-list" style="margin-top:12px;"></div>
  </div>
</div>

<!-- Add Lead Modal -->
<div class="modal-bg" id="add-lead-modal">
  <div class="modal" style="max-width:460px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 data-i18n="add_lead">Add Lead</h3>
      <button onclick="closeAddLeadModal()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <div class="form-group"><label data-i18n="name">Name</label><input type="text" id="lead-name" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;"></div>
    <div class="form-group"><label data-i18n="email">Email</label><input type="email" id="lead-email" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;"></div>
    <div class="form-group"><label data-i18n="phone">Phone</label><input type="text" id="lead-phone" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;"></div>
    <div class="form-group"><label data-i18n="notes">Notes</label><textarea id="lead-notes" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:60px;"></textarea></div>
    <button class="btn btn-primary" onclick="saveLead()" data-i18n="save_lead">Save Lead</button>
  </div>
</div>

<!-- Create Workflow Modal -->
<div class="modal-bg" id="create-workflow-modal">
  <div class="modal" style="max-width:500px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 data-i18n="create_workflow">Create Workflow</h3>
      <button onclick="closeCreateWorkflowModal()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <div class="form-group"><label data-i18n="workflow_name">Workflow Name</label><input type="text" id="wf-name" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;"></div>
    <div class="form-group"><label data-i18n="workflow_steps">Steps (JSON array)</label><textarea id="wf-steps" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:100px;" placeholder='[{"type":"email","template":"Hello {{name}}..."},{"type":"wait","duration":"1d"},{"type":"update_status","status":"contacted"}]'></textarea></div>
    <button class="btn btn-primary" onclick="saveWorkflow()" data-i18n="save_workflow">Save Workflow</button>
  </div>
</div>

<!-- ===== PAGE: MULTI-PLATFORM ===== -->
<div class="page" id="page-multiplatform">
  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
      <h2 data-i18n="multi_platform_scheduler">Multi-Platform Scheduler</h2>
      <div style="display:flex;gap:8px;">
        <button class="btn btn-primary" onclick="openMultiScheduleModal()" data-i18n="schedule_post">+ Schedule Post</button>
        <button class="btn" onclick="loadMultiQueue()" style="background:#e4e6eb;" data-i18n="refresh">Refresh</button>
      </div>
    </div>
  </div>
  <div class="section" style="overflow-x:auto;">
    <table>
      <thead><tr><th data-i18n="platforms">Platforms</th><th data-i18n="message">Message</th><th data-i18n="type">Type</th><th data-i18n="scheduled">Scheduled</th><th data-i18n="status">Status</th><th data-i18n="actions">Actions</th></tr></thead>
      <tbody id="multi-queue-table">
        <tr><td colspan="6" style="text-align:center;color:#65676b;padding:30px;" data-i18n="no_scheduled_posts">No scheduled posts.</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- Multi-Platform Schedule Modal -->
<div class="modal-bg" id="multi-schedule-modal">
  <div class="modal" style="max-width:560px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 data-i18n="schedule_post">Schedule Post</h3>
      <button onclick="closeMultiScheduleModal()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <div class="form-group">
      <label data-i18n="platforms">Platforms</label>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <label><input type="checkbox" class="mp-platform" value="facebook" checked> <span data-i18n="facebook">Facebook</span></label>
        <label><input type="checkbox" class="mp-platform" value="instagram"> <span data-i18n="instagram">Instagram</span></label>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="headline">Headline</label>
      <div style="display:flex;gap:6px;">
        <input type="text" id="mp-headline" placeholder="e.g. Special Offer!" maxlength="40" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <button class="btn btn-sm btn-outline" onclick="generateMpCopy()" style="white-space:nowrap;flex-shrink:0;" data-i18n="gen_ai">Gen AI</button>
        <a href="https://chat.openai.com" target="_blank" class="btn btn-sm btn-outline" style="white-space:nowrap;flex-shrink:0;text-decoration:none;background:#10a37f;color:#fff;border-color:#10a37f;" data-i18n="chatgpt" title="Open ChatGPT">ChatGPT</a>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="primary_text">Primary Text</label>
      <div style="display:flex;gap:6px;align-items:flex-start;">
        <textarea id="mp-message" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:60px;" data-i18n-placeholder="write_post" placeholder="Write your post..."></textarea>
        <button class="btn btn-sm btn-outline" onclick="generateMpCopy()" style="white-space:nowrap;flex-shrink:0;" data-i18n="gen_ai">Gen AI</button>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="ai_instruction">AI Instruction</label>
      <textarea id="mp-ai-instruction" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;resize:vertical;min-height:50px;" data-i18n-placeholder="ai_instruction_placeholder" placeholder="Describe what to promote. AI will use this to generate unique headlines and text."></textarea>
    </div>
    <div class="form-group">
      <label data-i18n="cta">Call to Action</label>
      <select id="mp-cta" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <option value="LEARN_MORE" data-i18n="cta_learn_more">Learn More</option>
        <option value="SIGN_UP" data-i18n="cta_sign_up">Sign Up</option>
        <option value="CONTACT_US" data-i18n="cta_contact_us">Contact Us</option>
        <option value="BOOK_NOW" data-i18n="cta_book_now">Book Now</option>
        <option value="GET_OFFER" data-i18n="cta_get_offer">Get Offer</option>
        <option value="SUBSCRIBE" data-i18n="cta_subscribe">Subscribe</option>
      </select>
    </div>
    <div class="form-group">
      <label data-i18n="dest_url">Destination URL</label>
      <input type="url" id="mp-link" placeholder="https://your-site.com/page" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
    </div>
    <div class="form-group">
      <label data-i18n="upload_media">Media</label>
      <div class="upload-zone" onclick="document.getElementById('mp-media-input').click()" style="border:2px dashed #ddd;border-radius:8px;padding:20px;text-align:center;cursor:pointer;">
        <div id="mp-upload-placeholder" style="color:#65676b;">
          <div style="font-size:2rem;margin-bottom:8px;">+</div>
          <div data-i18n="click_to_upload">Click to upload images (multiple allowed)</div>
          <div style="font-size:.75rem;margin-top:4px;" data-i18n="media_hint">Select multiple images for carousel, or one video</div>
        </div>
        <div id="mp-upload-preview" style="display:none;">
          <div id="mp-carousel-thumbs" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;"></div>
          <video id="mp-preview-video" style="max-height:120px;border-radius:6px;display:none;" controls></video>
          <div id="mp-preview-filename" style="font-size:.85rem;margin-top:6px;font-weight:600;"></div>
        </div>
      </div>
      <input type="file" id="mp-media-input" accept="image/*,video/*" style="display:none" multiple onchange="handleMpMediaUpload(this.files)">
      <div style="margin-top:6px;font-size:.8rem;color:#65676b;">
        <span><span data-i18n="images">Images</span>: <span id="mp-img-count">0</span> | </span>
        <a href="#" onclick="event.preventDefault();document.getElementById('mp-media-input').click();return false;" style="color:#1877f2;" data-i18n="add_more">Add more</a>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="schedule_label">Schedule (optional — leave empty for draft)</label>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <input type="date" id="mp-schedule-date" style="flex:1;min-width:140px;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <select id="mp-schedule-hour" style="padding:8px;border:1px solid #ddd;border-radius:6px;width:70px;">
          <option value="">HH</option><option>12</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option><option>8</option><option>9</option><option>10</option><option>11</option>
        </select>
        <span style="font-weight:700;">:</span>
        <select id="mp-schedule-min" style="padding:8px;border:1px solid #ddd;border-radius:6px;width:70px;">
          <option value="">MM</option><option>00</option><option>15</option><option>30</option><option>45</option>
        </select>
        <select id="mp-schedule-ampm" style="padding:8px;border:1px solid #ddd;border-radius:6px;width:75px;">
          <option value="AM">AM</option><option value="PM">PM</option>
        </select>
      </div>
      <input type="hidden" id="mp-schedule" value="">
    </div>
    <div class="form-group" style="display:flex;gap:8px;flex-wrap:wrap;">
      <button class="btn btn-primary" onclick="saveMultiDraft()" style="flex:1;" data-i18n="save_draft">Save as Draft</button>
      <button class="btn btn-success" onclick="saveMultiSchedule()" style="flex:1;" data-i18n="schedule_post">Schedule</button>
      <button class="btn btn-warn" onclick="publishMultiNow()" style="flex:1;" data-i18n="publish_post">Publish Now</button>
    </div>
    <div id="mp-status" style="margin-top:12px;font-size:.85rem;text-align:center;"></div>
  </div>
</div>

<!-- ===== PAGE: AUTO RESPONDER ===== -->
<div class="page" id="page-responder">
  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
      <h2 data-i18n="auto_responder">Social Media Auto Responder</h2>
      <div style="display:flex;gap:8px;align-items:center;">
        <label style="display:flex;align-items:center;gap:6px;font-size:14px;">
          <span data-i18n="enabled">Enabled</span>
          <input type="checkbox" id="responder-enabled" onchange="toggleResponder()" checked>
        </label>
        <button class="btn btn-primary" onclick="openAddRuleModal()" data-i18n="add_rule">+ Add Rule</button>
        <button class="btn" onclick="scanComments()" data-i18n="scan_comments">Scan & Respond</button>
        <button class="btn" onclick="loadResponder()" style="background:#e4e6eb;" data-i18n="refresh">Refresh</button>
      </div>
    </div>
  </div>

  <div class="grid" style="margin-bottom:16px;">
    <div class="section">
      <h3 style="margin-bottom:8px;" data-i18n="settings">Settings</h3>
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <label style="display:flex;align-items:center;gap:6px;font-size:14px;">
          <span data-i18n="use_ai">Use AI Responses</span>
          <input type="checkbox" id="responder-use-ai" onchange="toggleResponderAI()">
        </label>
        <div style="display:flex;gap:6px;align-items:center;flex:1;">
          <span style="font-size:13px;" data-i18n="default_response">Default:</span>
          <input type="text" id="responder-default" style="flex:1;padding:6px;border:1px solid #ddd;border-radius:6px;font-size:13px;" placeholder="Thank you for your comment!">
          <button class="btn btn-sm btn-primary" onclick="saveDefaultResponse()" data-i18n="save">Save</button>
        </div>
      </div>
    </div>
  </div>

  <div class="section" style="overflow-x:auto;">
    <h3 style="margin-bottom:8px;" data-i18n="auto_reply_rules">Auto-Reply Rules</h3>
    <div id="responder-rules"></div>
  </div>

  <div class="section" style="overflow-x:auto;">
    <h3 style="margin-bottom:8px;" data-i18n="response_log">Response Log</h3>
    <table>
      <thead><tr><th data-i18n="date">Date</th><th data-i18n="from">From</th><th data-i18n="comment">Comment</th><th data-i18n="response">Response</th><th data-i18n="status">Status</th></tr></thead>
      <tbody id="responder-log-table">
        <tr><td colspan="5" style="text-align:center;color:#65676b;padding:30px;" data-i18n="no_responses">No responses yet.</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- Add Rule Modal -->
<div class="modal-bg" id="add-rule-modal">
  <div class="modal" style="max-width:460px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 data-i18n="add_rule">Add Rule</h3>
      <button onclick="closeAddRuleModal()" style="background:none;border:none;font-size:1.4rem;cursor:pointer;">x</button>
    </div>
    <div class="form-group"><label data-i18n="keyword">Keyword</label><input type="text" id="rule-keyword" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;" placeholder="e.g. price, help, thanks"></div>
    <div class="form-group"><label data-i18n="response">Response</label><textarea id="rule-response" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:60px;" placeholder="Thank you for your question!"></textarea></div>
    <div class="form-group"><label data-i18n="platform">Platform</label>
      <select id="rule-platform" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
        <option value="all" data-i18n="all">All</option>
        <option value="facebook">Facebook</option>
        <option value="instagram">Instagram</option>
      </select>
    </div>
    <button class="btn btn-primary" onclick="saveRule()" data-i18n="save_rule">Save Rule</button>
  </div>
</div>

<!-- ===== Multi-tenant login overlay ===== -->
<!-- Hidden by default. Only appears if the backend says this browser needs to
     log in (i.e. more than one tenant exists and this session isn't authenticated
     for the one being requested). Single-studio setups never see this. -->
<div id="tenant-login-overlay" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:99999; align-items:center; justify-content:center;">
  <div style="background:#fff; border-radius:12px; padding:32px; width:340px; max-width:90vw; box-shadow:0 10px 40px rgba(0,0,0,0.3);">
    <h2 style="margin:0 0 4px; font-size:20px;" data-i18n="login_title">Sign In</h2>
    <p style="margin:0 0 20px; color:#666; font-size:13px;" data-i18n="login_desc">This dashboard has multiple clients configured. Enter your credentials.</p>
    <div style="margin-bottom:12px;">
      <label style="display:block; font-size:12px; color:#444; margin-bottom:4px;" data-i18n="login_client_id">Client</label>
      <select id="tenant-login-id" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;"></select>
    </div>
    <div style="margin-bottom:16px;">
      <label style="display:block; font-size:12px; color:#444; margin-bottom:4px;" data-i18n="password">Password</label>
      <input id="tenant-login-pw" type="password" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;" onkeydown="if(event.key==='Enter') tenantLogin()">
    </div>
    <div id="tenant-login-error" style="color:#c0392b; font-size:12px; margin-bottom:12px; display:none;"></div>
    <button onclick="tenantLogin()" style="width:100%; padding:10px; background:#2563eb; color:#fff; border:none; border-radius:6px; font-weight:600; cursor:pointer;" data-i18n="sign_in">Sign In</button>
  </div>
</div>

<!-- Small badge showing which studio you're logged in as, top-right corner -->
<div id="tenant-badge" style="display:none; position:fixed; top:10px; right:10px; background:#111827; color:#fff; padding:6px 12px; border-radius:20px; font-size:12px; z-index:9999; display:flex; align-items:center; gap:8px;">
  <span id="tenant-badge-name"></span>
  <a href="#" onclick="tenantLogout(); return false;" style="color:#93c5fd; text-decoration:none;" data-i18n="logout">logout</a>
</div>

<!-- Botón para conectar QuickBooks (Intuit) -->
<div id="qb-connect-widget" style="position:fixed; bottom:10px; right:10px; z-index:9999;">
  <button id="qb-connect-btn" onclick="connectQuickBooks()" style="background:#2ca01c; color:#fff; border:none; padding:8px 14px; border-radius:6px; font-size:12px; cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,0.2);">
    Connect QuickBooks
  </button>
</div>

<script>
async function connectQuickBooks() {
  const params = new URLSearchParams(window.location.search);
  const tenant = params.get('tenant') || 'default';
  try {
    const resp = await fetch('/api/quickbooks/connect-url?tenant=' + encodeURIComponent(tenant));
    const data = await resp.json();
    if (data.success) {
      window.location = data.connect_url;
    } else {
      alert(data.error || 'Could not generate connection URL');
    }
  } catch (e) {
    alert('Could not connect to server');
  }
}
async function refreshQbButton() {
  const params = new URLSearchParams(window.location.search);
  const tenant = params.get('tenant') || 'default';
  try {
    const resp = await fetch('/api/quickbooks/status?tenant=' + encodeURIComponent(tenant));
    const data = await resp.json();
    const btn = document.getElementById('qb-connect-btn');
    if (data.connected) {
      btn.textContent = 'QuickBooks connected ✓';
      btn.style.background = '#6b7280';
    }
  } catch (e) {}
}
document.addEventListener('DOMContentLoaded', refreshQbButton);
</script>

<script>
(function() {
  async function checkAuth() {
    try {
      const params = new URLSearchParams(window.location.search);
      const requestedTenant = params.get('tenant') || '';
      const who = await fetch('/api/whoami').then(r => r.json());
      const tenantsResp = await fetch('/api/tenants').then(r => r.json());
      const tenants = tenantsResp.tenants || {};
      const tenantCount = Object.keys(tenants).length;

      if (tenantCount <= 1) {
        return;
      }
      var sel = document.getElementById('tenant-login-id');
      sel.innerHTML = '';
      Object.keys(tenants).forEach(function(tid) {
        var opt = document.createElement('option');
        opt.value = tid;
        opt.textContent = tenants[tid].name || tid;
        sel.appendChild(opt);
      });
      if (!who.has_session) {
        if (requestedTenant) sel.value = requestedTenant;
        document.getElementById('tenant-login-overlay').style.display = 'flex';
        return;
      }
      document.getElementById('tenant-badge').style.display = 'flex';
      document.getElementById('tenant-badge-name').textContent = who.tenant_id;
    } catch (e) {
      console.warn('Auth check failed', e);
    }
  }

  window.tenantLogin = async function() {
    const tenant_id = document.getElementById('tenant-login-id').value.trim() || 'default';
    const password = document.getElementById('tenant-login-pw').value;
    const errEl = document.getElementById('tenant-login-error');
    errEl.style.display = 'none';
    try {
      const resp = await fetch('/api/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tenant_id, password})
      });
      const data = await resp.json();
      if (data.success) {
        const url = new URL(window.location);
        url.searchParams.set('tenant', tenant_id);
        window.location = url.toString();
      } else {
        errEl.textContent = data.error || 'Login failed';
        errEl.style.display = 'block';
      }
    } catch (e) {
      errEl.textContent = 'Could not connect to server';
      errEl.style.display = 'block';
    }
  };

  window.tenantLogout = async function() {
    await fetch('/api/logout', {method: 'POST'});
    const url = new URL(window.location);
    url.searchParams.delete('tenant');
    window.location = url.toString();
  };

  document.addEventListener('DOMContentLoaded', checkAuth);
})();
</script>

<!-- Panel de los 3 numeros que importan: costo por lead, % que agenda, % que se inscribe -->
<div id="kpi-panel-widget" style="position:fixed; bottom:55px; right:10px; z-index:9998; background:#fff; border-radius:10px; box-shadow:0 2px 12px rgba(0,0,0,0.15); padding:14px 18px; font-size:12px; min-width:280px;">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <strong style="font-size:13px;" data-i18n="kpi_title">&#x1f4ca; Is it working?</strong>
    <a href="#" onclick="loadKpiPanel(); return false;" style="font-size:11px; color:#2563eb; text-decoration:none;" data-i18n="kpi_refresh">&#x21bb; refresh</a>
  </div>
  <div id="kpi-panel-body" style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; text-align:center;">
    <div><div id="kpi-cpl" style="font-size:18px; font-weight:700; color:#111827;">--</div><div style="color:#6b7280;" data-i18n="kpi_cost_per_lead">cost/lead</div></div>
    <div><div id="kpi-booking" style="font-size:18px; font-weight:700; color:#111827;">--</div><div style="color:#6b7280;" data-i18n="kpi_booked">booked</div></div>
    <div><div id="kpi-enroll" style="font-size:18px; font-weight:700; color:#111827;">--</div><div style="color:#6b7280;" data-i18n="kpi_enrolled">enrolled</div></div>
  </div>
  <div id="kpi-confidence-note" style="margin-top:10px; padding-top:8px; border-top:1px solid #eee; color:#6b7280; font-size:11px;"></div>
</div>

<script>
async function loadKpiPanel() {
  try {
    const params = new URLSearchParams(window.location.search);
    const tenant = params.get('tenant');
    let url = '/api/kpi-panel';
    if (tenant) url += '?tenant=' + encodeURIComponent(tenant);
    const resp = await fetch(url);
    const d = await resp.json();
    document.getElementById('kpi-cpl').textContent = d.cost_per_lead != null ? ('$' + d.cost_per_lead) : '--';
    document.getElementById('kpi-booking').textContent = d.booking_rate_pct != null ? (d.booking_rate_pct + '%') : '--';
    document.getElementById('kpi-enroll').textContent = d.enrollment_rate_pct != null ? (d.enrollment_rate_pct + '%') : '--';
    document.getElementById('kpi-confidence-note').textContent = _t('confidence_' + d.confidence) || d.confidence_note || '';
  } catch (e) {
    console.warn('Could not load KPI panel', e);
  }
}
document.addEventListener('DOMContentLoaded', loadKpiPanel);
</script>

</body>
</html>
"""

# ==================== QUICKBOOKS CONNECTOR (Intuit OAuth2) ====================
# Just what Esteban asked for: a real "Connect to QuickBooks" link, using
# Intuit's actual OAuth2 endpoints (verified against Intuit's own docs, 2026).
# No billing logic here -- he uses QuickBooks itself for that. This only
# handles the connection handshake and stores the resulting tokens.

QB_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_SCOPE = "com.intuit.quickbooks.accounting"


class QuickBooksConnector:
    def __init__(self, connections_path='quickbooks_connections.json'):
        self.client_id = os.environ.get('QB_CLIENT_ID', '')
        self.client_secret = os.environ.get('QB_CLIENT_SECRET', '')
        self.redirect_uri = os.environ.get('QB_REDIRECT_URI', '')
        self.environment = os.environ.get('QB_ENVIRONMENT', 'sandbox')  # 'sandbox' or 'production'
        self.connections_path = connections_path

    def is_configured(self):
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def build_auth_url(self, state):
        """This is the URL Esteban asked for -- send the user's browser here to connect."""
        from urllib.parse import urlencode
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': QB_SCOPE,
            'state': state,
        }
        return f"{QB_AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_tokens(self, code, realm_id):
        import base64
        auth_header = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = requests.post(
            QB_TOKEN_URL,
            headers={
                'Authorization': f'Basic {auth_header}',
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': self.redirect_uri,
            }
        )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            return None, data.get('error_description', f'HTTP {resp.status_code}')
        data['realm_id'] = realm_id
        data['connected_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        data['environment'] = self.environment
        return data, None

    def save_connection(self, tenant_id, token_data):
        connections = {}
        if os.path.exists(self.connections_path):
            try:
                with open(self.connections_path, 'r', encoding='utf-8') as f:
                    connections = json.load(f)
            except Exception:
                pass
        connections[tenant_id] = token_data
        try:
            with open(self.connections_path, 'w', encoding='utf-8') as f:
                json.dump(connections, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[QuickBooksConnector] WARNING: could not save connection: {e}")
            return False

    def get_connection(self, tenant_id):
        if os.path.exists(self.connections_path):
            try:
                with open(self.connections_path, 'r', encoding='utf-8') as f:
                    return json.load(f).get(tenant_id)
            except Exception:
                pass
        return None


quickbooks_connector = QuickBooksConnector()

# ==================== TENANT MANAGER (multi-studio support) ====================
# Lets this run for more than one studio at once, each fully isolated:
# its own Meta credentials, its own spend cap, its own campaigns, its own
# audit log. The original single-studio setup keeps working unchanged as
# the 'default' tenant -- nothing breaks for an existing install.

class TenantManager:
    def __init__(self, config_path='tenants.json', default_agent=None):
        self.config_path = config_path
        self._agents = {}
        if default_agent is not None:
            self._agents['default'] = default_agent  # reuse the already-running single-tenant agent
        self.tenants = self._load()

    def _load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for tid, cfg in data.items():
                    cfg.setdefault('role', 'admin' if tid == 'default' else 'client')
                return data
            except Exception as e:
                print(f"[TenantManager] WARNING: could not read {self.config_path}: {e}")
        # Seed with just 'default' -- it uses whatever is already in .env.txt,
        # nothing to configure here for a single-studio setup.
        seed = {'default': {'name': 'Default Studio', 'industry': 'martialarts', 'role': 'admin', 'uses_env_credentials': True}}
        self._save(seed)
        return seed

    def _save(self, data=None):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data if data is not None else self.tenants, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[TenantManager] WARNING: could not save {self.config_path}: {e}")
            return False

    def list_tenants(self):
        # Never expose secrets in a listing
        hidden = ('token', 'secret', 'password')
        return {tid: {k: v for k, v in cfg.items() if not any(h in k.lower() for h in hidden)}
                for tid, cfg in self.tenants.items()}

    def create_tenant(self, tenant_id, config):
        if tenant_id in self.tenants:
            return False, "A tenant with this ID already exists"
        required = ['meta_access_token', 'meta_ad_account_id', 'password']
        missing = [f for f in required if not config.get(f)]
        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"
        password = config.pop('password')
        config['password_hash'] = generate_password_hash(password)
        config.setdefault('name', tenant_id)
        config.setdefault('industry', 'service')
        config.setdefault('role', 'client')
        config.setdefault('max_total_daily_budget', 50)
        config.setdefault('auto_budget_increase_enabled', False)
        self.tenants[tenant_id] = config
        self._save()
        safety_guard.log('create_tenant', allowed=True, reason=f"New tenant '{tenant_id}' created", tenant_id=tenant_id)
        return True, "OK"

    def verify_login(self, tenant_id, password):
        cfg = self.tenants.get(tenant_id)
        if not cfg:
            return False
        # 'default' with no password set (classic single-studio setup) -- no login wall.
        if not cfg.get('password_hash'):
            return tenant_id == 'default'
        return check_password_hash(cfg['password_hash'], password)

    def get_agent(self, tenant_id='default'):
        if tenant_id in self._agents:
            return self._agents[tenant_id]
        if tenant_id not in self.tenants:
            tenant_id = 'default'
        cfg = self.tenants.get(tenant_id, {})

        if cfg.get('uses_env_credentials'):
            meta_credentials = {
                'access_token': os.environ.get('META_ACCESS_TOKEN', ''),
                'ad_account_id': os.environ.get('META_AD_ACCOUNT_ID', ''),
                'app_id': os.environ.get('META_APP_ID'),
                'app_secret': os.environ.get('META_APP_SECRET'),
                'page_token': os.environ.get('META_PAGE_TOKEN'),
            }
            ai_key = os.environ.get('GROQ_API_KEY') or os.environ.get('GEMINI_API_KEY')
        else:
            meta_credentials = {
                'access_token': cfg.get('meta_access_token', ''),
                'ad_account_id': cfg.get('meta_ad_account_id', ''),
                'app_id': cfg.get('meta_app_id'),
                'app_secret': cfg.get('meta_app_secret'),
                'page_token': cfg.get('meta_page_token'),
            }
            ai_key = cfg.get('ai_api_key')

        tenant_safety_guard = SafetyGuard(audit_log_path=f'audit_log_{tenant_id}.jsonl')
        tenant_safety_guard.max_total_daily_budget = float(cfg.get('max_total_daily_budget', 50))
        tenant_safety_guard.auto_budget_increase_enabled = bool(cfg.get('auto_budget_increase_enabled', False))

        agent = UniversalMetaAdsAgent(
            meta_credentials, ai_api_key=ai_key,
            tenant_id=tenant_id, safety_guard_instance=tenant_safety_guard,
            campaigns_file=f'campaigns_{tenant_id}.json' if tenant_id != 'default' else None
        )
        self._agents[tenant_id] = agent
        return agent


def create_web_interface(ads_agent, tenant_manager=None):
    app = Flask(__name__)
    secret = os.environ.get('SECRET_KEY', '')
    if not secret or secret in ('meta-ads-secret-key-2025', 'cambia-esto-por-algo-unico'):
        secret_file = os.path.join(os.path.dirname(__file__), '.secret_key')
        if os.path.exists(secret_file):
            with open(secret_file) as f:
                secret = f.read().strip()
        else:
            import secrets
            secret = secrets.token_hex(32)
            with open(secret_file, 'w') as f:
                f.write(secret)
    app.config['SECRET_KEY'] = secret
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB upload limit

    _rate_limits = {}
    RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_MAX = 60

    def _check_rate_limit(ip, window=RATE_LIMIT_WINDOW, max_req=RATE_LIMIT_MAX):
        now = time.time()
        if ip not in _rate_limits:
            _rate_limits[ip] = []
        _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < window]
        if len(_rate_limits[ip]) >= max_req:
            return False
        _rate_limits[ip].append(now)
        return True

    @app.before_request
    def rate_limit():
        if request.method in ('POST', 'DELETE'):
            ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
            if not _check_rate_limit(ip):
                return jsonify({'success': False, 'error': 'Rate limit exceeded. Try again later.'}), 429

    if os.environ.get('PRODUCTION'):
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

        @app.before_request
        def redirect_to_https():
            if request.headers.get('X-Forwarded-Proto') == 'http':
                url = request.url.replace('http://', 'https://', 1)
                return redirect(url, code=301)

    def resolve_agent():
        """Which studio's agent this request is for. ?tenant=<id> or header X-Tenant-Id.
        No tenant specified -> the original single-studio agent, unchanged behavior."""
        tid = request.args.get('tenant') or request.headers.get('X-Tenant-Id') or 'default'
        if tenant_manager is None or tid == 'default':
            return ads_agent
        return tenant_manager.get_agent(tid)

    def current_session_tenant():
        return session.get('tenant_id')

    def require_tenant_auth(view_func):
        """Blocks a request for tenant X unless the browser is logged in as tenant X.
        The 'default' studio (classic single-studio setup, no other tenants ever
        created) keeps working with zero login friction -- this only kicks in
        once real multi-tenant accounts exist."""
        from functools import wraps

        @wraps(view_func)
        def wrapped(*args, **kwargs):
            tid = request.args.get('tenant') or request.headers.get('X-Tenant-Id') or 'default'
            if tid == 'default' and (tenant_manager is None or len(tenant_manager.tenants) <= 1):
                return view_func(*args, **kwargs)
            if current_session_tenant() != tid:
                return jsonify({'success': False, 'error': f"Not authenticated for tenant '{tid}'. Please log in first."}), 401
            return view_func(*args, **kwargs)
        return wrapped

    def admin_required(view_func):
        """Only allows access if the logged-in tenant has role='admin'."""
        from functools import wraps

        @wraps(view_func)
        def wrapped(*args, **kwargs):
            tid = current_session_tenant() or 'default'
            if tenant_manager and tid in tenant_manager.tenants:
                role = tenant_manager.tenants[tid].get('role', 'client')
                if role == 'admin':
                    return view_func(*args, **kwargs)
            elif tid == 'default' and (tenant_manager is None or len(tenant_manager.tenants) <= 1):
                return view_func(*args, **kwargs)
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
        return wrapped

    @app.route('/api/login', methods=['POST'])
    def api_login():
        data = request.get_json() or {}
        tenant_id = data.get('tenant_id', 'default')
        password = data.get('password', '')
        if tenant_manager is None:
            return jsonify({'success': False, 'error': 'Tenant manager not initialized'})
        if tenant_manager.verify_login(tenant_id, password):
            session['tenant_id'] = tenant_id
            safety_guard.log('login', allowed=True, reason='Login successful', tenant_id=tenant_id)
            return jsonify({'success': True, 'tenant_id': tenant_id})
        safety_guard.log('login', allowed=False, reason='Invalid password or tenant not found', tenant_id=tenant_id)
        return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

    @app.route('/api/logout', methods=['POST'])
    def api_logout():
        session.pop('tenant_id', None)
        return jsonify({'success': True})

    @app.route('/api/health')
    def api_health():
        try:
            token_valid = resolve_agent().meta_api.validate_token()
            expires_at = getattr(resolve_agent().meta_api, 'token_expires_at', None)
            return jsonify({
                'status': 'ok',
                'token_valid': token_valid,
                'token_expires_at': expires_at,
                'campaigns_loaded': len(resolve_agent().campaigns),
                'tenants_count': len(tenant_manager.tenants) if tenant_manager else 1,
            })
        except Exception as e:
            return jsonify({'status': 'error', 'error': str(e)}), 500

    @app.route('/api/whoami')
    def api_whoami():
        raw_tid = current_session_tenant()
        has_session = raw_tid is not None
        tid = raw_tid or 'default'
        role = 'client'
        if tenant_manager and tid in tenant_manager.tenants:
            role = tenant_manager.tenants[tid].get('role', 'client')
        token_valid = resolve_agent().meta_api.validate_token()
        expires_at = getattr(resolve_agent().meta_api, 'token_expires_at', None)
        return jsonify({
            'success': True, 'tenant_id': tid, 'role': role,
            'has_session': has_session,
            'token_valid': token_valid, 'token_expires_at': expires_at
        })

    scheduler = ContentScheduler(ads_agent.meta_api)

    @app.route('/')
    def home():
        resp = app.response_class(HTML_TEMPLATE, content_type='text/html; charset=utf-8')
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    @app.route('/api/campaigns')
    def api_campaigns():
        return jsonify(resolve_agent().get_all_campaigns())

    @app.route('/api/campaign/<campaign_id>/performance')
    def api_campaign_performance(campaign_id):
        perf = resolve_agent().get_campaign_performance(campaign_id)
        if not perf:
            return jsonify({'error': 'Campaign not found'}), 404
        return jsonify(perf)

    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    @app.route('/uploads/<filename>')
    def serve_upload(filename):
        return send_from_directory(UPLOAD_FOLDER, filename)

    @app.route('/api/upload-media', methods=['POST'])
    @require_tenant_auth
    def api_upload_media():
        from werkzeug.utils import secure_filename as _secure_fn
        if 'media' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'})
        file = request.files['media']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        allowed_images = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
        allowed_videos = {'mp4', 'mov', 'avi', 'webm'}
        if ext not in allowed_images and ext not in allowed_videos:
            return jsonify({'success': False, 'error': f'File type .{ext} not supported'})
        safe_name = _secure_fn(file.filename) or f"upload.{ext}"
        filename = f"{int(time.time())}_{safe_name}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        is_video = ext in allowed_videos
        return jsonify({
            'success': True,
            'url': f'/uploads/{filename}',
            'is_video': is_video,
            'filename': filename
        })

    @app.route('/api/create-campaign', methods=['POST'])
    @require_tenant_auth
    def api_create_campaign():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data received'})
        try:
            result = resolve_agent().create_campaign_for_business(data)
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/delete-campaign/<campaign_id>', methods=['POST'])
    @require_tenant_auth
    def api_delete_campaign(campaign_id):
        try:
            agent = resolve_agent()
            if campaign_id in agent.campaigns:
                agent.campaigns[campaign_id]['status'] = 'TRASHED'
                agent._save_campaigns()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/restore-campaign/<campaign_id>', methods=['POST'])
    @require_tenant_auth
    def api_restore_campaign(campaign_id):
        try:
            agent = resolve_agent()
            if campaign_id in agent.campaigns:
                agent.campaigns[campaign_id]['status'] = 'ARCHIVED'
                agent._save_campaigns()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/delete-campaign-forever/<campaign_id>', methods=['POST'])
    @require_tenant_auth
    def api_delete_campaign_forever(campaign_id):
        try:
            agent = resolve_agent()
            if campaign_id in agent.campaigns:
                del agent.campaigns[campaign_id]
                agent._save_campaigns()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/optimize/<campaign_id>', methods=['GET', 'POST'])
    @require_tenant_auth
    def api_optimize(campaign_id):
        agent = resolve_agent()
        perf = agent.get_campaign_performance(campaign_id)
        if not perf:
            return jsonify({'success': False, 'error': 'Campaign not found'})
        opts = agent.performance_optimizer.generate_optimizations(perf['analysis'])
        if request.method == 'GET':
            return jsonify({'success': True, 'optimizations': opts, 'preview': True})
        results = agent.performance_optimizer.apply_optimizations(opts, campaign_id)
        return jsonify({'success': True, 'optimizations': opts, 'results': results})

    @app.route('/api/optimize-all', methods=['POST'])
    @require_tenant_auth
    def api_optimize_all():
        resolve_agent().optimize_campaigns()
        return jsonify({'success': True})

    @app.route('/api/token-status')
    def api_token_status():
        expires_at = getattr(resolve_agent().meta_api, 'token_expires_at', None)
        return jsonify({'expires_at': expires_at})

    @app.route('/api/token-refresh', methods=['POST'])
    @require_tenant_auth
    def api_token_refresh():
        agent = resolve_agent()
        ok = agent.meta_api.refresh_access_token()
        if ok:
            safety_guard.log('token_refresh', allowed=True, reason='Token refreshed successfully')
            return jsonify({'success': True, 'message': 'Token refreshed'})
        return jsonify({'success': False, 'error': 'Token refresh failed. Update token manually in Settings.'}), 400

    @app.route('/api/safety-status')
    def api_safety_status():
        agent = resolve_agent()
        sg = agent.safety_guard
        committed = sg.current_committed_budget(agent.campaigns)
        return jsonify({
            'success': True,
            'tenant_id': getattr(agent, 'tenant_id', 'default'),
            'max_total_daily_budget': sg.max_total_daily_budget,
            'committed_daily_budget': committed,
            'remaining_daily_budget': max(sg.max_total_daily_budget - committed, 0),
            'auto_budget_increase_enabled': sg.auto_budget_increase_enabled
        })

    @app.route('/api/safety/toggle-auto-budget', methods=['POST'])
    @require_tenant_auth
    def api_toggle_auto_budget():
        agent = resolve_agent()
        sg = agent.safety_guard
        sg.auto_budget_increase_enabled = not sg.auto_budget_increase_enabled
        return jsonify({'success': True, 'auto_budget_increase_enabled': sg.auto_budget_increase_enabled})

    @app.route('/api/safety/update-budget', methods=['POST'])
    @require_tenant_auth
    def api_update_budget():
        data = request.get_json()
        new_max = data.get('max_total_daily_budget')
        if not new_max or not isinstance(new_max, (int, float)) or new_max < 1:
            return jsonify({'success': False, 'error': 'Invalid budget amount'})
        agent = resolve_agent()
        agent.safety_guard.max_total_daily_budget = float(new_max)
        return jsonify({'success': True, 'max_total_daily_budget': float(new_max)})

    @app.route('/api/industry-config')
    def api_industry_config_list():
        return jsonify({'success': True, 'industries': config_store.list_industries(), 'config': config_store.data})

    @app.route('/api/industry-config/<industry>', methods=['GET'])
    def api_industry_config_get(industry):
        return jsonify({'success': True, 'industry': industry, 'config': config_store.get(industry)})

    @app.route('/api/industry-config/<industry>', methods=['POST'])
    @require_tenant_auth
    def api_industry_config_update(industry):
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data received'})
        ok = config_store.update(industry, data)
        safety_guard.log('update_industry_config', allowed=ok, reason=f"Edited targeting/budget rules for '{industry}'", industry=industry, changes=data)
        return jsonify({'success': ok, 'industry': industry, 'config': config_store.get(industry)})

    @app.route('/api/audit-log')
    def api_audit_log():
        agent = resolve_agent()
        sg = agent.safety_guard
        limit = int(request.args.get('limit', 100))
        entries = []
        try:
            if os.path.exists(sg.audit_log_path):
                with open(sg.audit_log_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                for line in lines[-limit:]:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
        entries.reverse()  # most recent first
        return jsonify({'success': True, 'entries': entries})

    @app.route('/api/tenants')
    def api_tenants_list():
        if tenant_manager is None:
            return jsonify({'success': True, 'tenants': {'default': {'name': 'Default Studio'}}})
        return jsonify({'success': True, 'tenants': tenant_manager.list_tenants()})

    @app.route('/api/tenants', methods=['POST'])
    @admin_required
    def api_tenants_create():
        if tenant_manager is None:
            return jsonify({'success': False, 'error': 'Tenant manager not initialized'})
        data = request.get_json()
        if not data or not data.get('tenant_id'):
            return jsonify({'success': False, 'error': 'tenant_id is required'})
        tenant_id = data.pop('tenant_id')
        ok, msg = tenant_manager.create_tenant(tenant_id, data)
        return jsonify({'success': ok, 'message': msg, 'tenant_id': tenant_id if ok else None})

    @app.route('/api/tenants/<tenant_id>', methods=['DELETE'])
    @admin_required
    def api_tenants_delete(tenant_id):
        if tenant_manager is None:
            return jsonify({'success': False, 'error': 'Tenant manager not initialized'})
        if tenant_id == 'default':
            return jsonify({'success': False, 'error': 'Cannot delete the default admin tenant'})
        if tenant_id not in tenant_manager.tenants:
            return jsonify({'success': False, 'error': 'Tenant not found'})
        del tenant_manager.tenants[tenant_id]
        tenant_manager._save()
        campaigns_file = os.path.join(os.path.dirname(__file__), f'campaigns_{tenant_id}.json')
        if os.path.exists(campaigns_file):
            try: os.remove(campaigns_file)
            except: pass
        return jsonify({'success': True})

    # ==================== QuickBooks (Intuit) connect ====================

    @app.route('/api/quickbooks/connect-url')
    def api_quickbooks_connect_url():
        """The URL to connect directly to Intuit -- send the user's browser here."""
        if not quickbooks_connector.is_configured():
            return jsonify({
                'success': False,
                'error': 'QB_CLIENT_ID, QB_CLIENT_SECRET, or QB_REDIRECT_URI missing from .env.txt. '
                          'Create an app at https://developer.intuit.com/ to get credentials.'
            })
        tenant_id = request.args.get('tenant', 'default')
        state = f"{tenant_id}:{os.urandom(8).hex()}"
        session['qb_oauth_state'] = state
        return jsonify({'success': True, 'connect_url': quickbooks_connector.build_auth_url(state)})

    @app.route('/quickbooks/callback')
    def quickbooks_callback():
        """Intuit redirects here after the user approves the connection."""
        error = request.args.get('error')
        if error:
            return f"<h2>QuickBooks connection cancelled</h2><p>{error}</p><a href='/'>Back</a>", 400

        code = request.args.get('code')
        realm_id = request.args.get('realmId')
        state = request.args.get('state', '')

        if not code or not realm_id:
            return "<h2>Missing parameters in Intuit response</h2><a href='/'>Back</a>", 400
        if state != session.get('qb_oauth_state'):
            safety_guard.log('quickbooks_connect', allowed=False, reason='state mismatch (posible CSRF)')
            return "<h2>Invalid security token, please try connecting again</h2><a href='/'>Back</a>", 400

        tenant_id = state.split(':', 1)[0] if ':' in state else 'default'
        token_data, err = quickbooks_connector.exchange_code_for_tokens(code, realm_id)
        if err:
            safety_guard.log('quickbooks_connect', allowed=False, reason=err, tenant_id=tenant_id)
            return f"<h2>Could not complete QuickBooks connection</h2><p>{err}</p><a href='/'>Back</a>", 400

        quickbooks_connector.save_connection(tenant_id, token_data)
        safety_guard.log('quickbooks_connect', allowed=True, reason='Connected successfully', tenant_id=tenant_id, realm_id=realm_id)
        return f"""
        <html><body style="font-family:sans-serif; text-align:center; padding:60px;">
        <h2>✅ QuickBooks connected successfully</h2>
        <p>Company (realm ID): {realm_id}</p>
        <p><a href="/">Back to dashboard</a></p>
        </body></html>
        """

    @app.route('/api/quickbooks/status')
    def api_quickbooks_status():
        tenant_id = request.args.get('tenant', 'default')
        conn = quickbooks_connector.get_connection(tenant_id)
        if not conn:
            return jsonify({'success': True, 'connected': False})
        return jsonify({
            'success': True,
            'connected': True,
            'realm_id': conn.get('realm_id'),
            'connected_at': conn.get('connected_at'),
            'environment': conn.get('environment')
        })


    @app.route('/api/generate-ad-copy', methods=['POST'])
    @require_tenant_auth
    def api_generate_ad_copy():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data'})
        language = data.get('language', 'en')
        data['ai_api_key'] = getattr(resolve_agent(), 'ai_api_key', '')
        data['ai_provider'] = getattr(resolve_agent(), 'ai_provider', 'groq')
        engine = AIStrategyEngine()
        engine.ai_api_key = data['ai_api_key']
        engine.ai_provider = data['ai_provider']
        result = engine.generate_ad_copy(data, language)
        return jsonify({'success': True, 'headlines': result['headlines'], 'messages': result['messages']})

    @app.route('/api/lead-forms')
    def api_lead_forms():
        page_id = getattr(resolve_agent().meta_api, 'page_id', '117339024950778')
        forms = resolve_agent().meta_api.get_lead_forms(page_id)
        return jsonify({'success': True, 'forms': forms})

    @app.route('/api/leads/<form_id>')
    def api_leads(form_id):
        leads = resolve_agent().meta_api.get_leads(form_id)
        return jsonify({'success': True, 'leads': leads})

    @app.route('/api/audiences')
    def api_audiences_list():
        try:
            url = f"{resolve_agent().meta_api.base_url}/{resolve_agent().meta_api.ad_account_id}/customaudiences"
            params = {'access_token': resolve_agent().meta_api.access_token, 'fields': 'id,name,description,tag', 'limit': 100}
            r = requests.get(url, params=params)
            data = r.json().get('data', [])
            return jsonify({'success': True, 'audiences': data})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e), 'audiences': []})

    @app.route('/api/audiences/create', methods=['POST'])
    @require_tenant_auth
    def api_audiences_create():
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'success': False, 'error': 'Audience name is required'})
        try:
            agent = resolve_agent()
            payload = {
                'name': data['name'],
                'subtype': data.get('subtype', 'CUSTOM'),
                'description': data.get('description', ''),
                'prefill': data.get('prefill', 'NONE'),
            }
            url = f"{agent.meta_api.base_url}/{agent.meta_api.ad_account_id}/customaudiences"
            r = requests.post(url, json=payload, params={'access_token': agent.meta_api.access_token})
            result = r.json()
            if 'id' in result:
                return jsonify({'success': True, 'audience_id': result['id'], 'name': data['name']})
            return jsonify({'success': False, 'error': result.get('error', {}).get('message', 'Unknown error')})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/audiences/lookalike', methods=['POST'])
    @require_tenant_auth
    def api_audiences_lookalike():
        data = request.get_json()
        if not data or not data.get('name') or not data.get('source_audience_id'):
            return jsonify({'success': False, 'error': 'Name and source audience ID are required'})
        try:
            agent = resolve_agent()
            payload = {
                'name': data['name'],
                'origin': [{'id': data['source_audience_id'], 'type': 'custom_audience'}],
                'target_spec': {
                    'geo_locations': {'countries': [data.get('country', 'US')]},
                    'start': data.get('start', 1),
                    'end': data.get('end', 10),
                },
            }
            url = f"{agent.meta_api.base_url}/{agent.meta_api.ad_account_id}/customaudiences"
            r = requests.post(url, json=payload, params={'access_token': agent.meta_api.access_token})
            result = r.json()
            if 'id' in result:
                return jsonify({'success': True, 'audience_id': result['id'], 'name': data['name']})
            return jsonify({'success': False, 'error': result.get('error', {}).get('message', 'Unknown error')})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/audiences/delete/<audience_id>', methods=['POST', 'DELETE'])
    @require_tenant_auth
    def api_audiences_delete(audience_id):
        try:
            agent = resolve_agent()
            url = f"{agent.meta_api.base_url}/{audience_id}"
            r = requests.delete(url, params={'access_token': agent.meta_api.access_token})
            return jsonify({'success': r.json().get('success', True)})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/lead-forms/create', methods=['POST'])
    @require_tenant_auth
    def api_lead_forms_create():
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'success': False, 'error': 'Form name is required'})
        try:
            agent = resolve_agent()
            page_id = data.get('page_id') or getattr(agent.meta_api, 'page_id', None)
            if not page_id:
                page_id = agent.meta_api.get_page_id()
            url = f"{agent.meta_api.base_url}/{page_id}/leadgen_forms"
            payload = {
                'name': data['name'],
                'qualifiers': data.get('qualifiers', ['FULL_NAME', 'EMAIL', 'PHONE_NUMBER']),
                'questions': data.get('questions', []),
                'privacy_policy': data.get('privacy_policy', {'url': 'https://example.com/privacy'}),
                'follow_up_action_url': data.get('follow_up_action_url', ''),
            }
            r = requests.post(url, json=payload, params={'access_token': agent.meta_api.access_token})
            result = r.json()
            if 'id' in result:
                return jsonify({'success': True, 'form_id': result['id'], 'name': data['name']})
            return jsonify({'success': False, 'error': result.get('error', {}).get('message', 'Unknown error')})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/accounts')
    def api_accounts():
        sensitive = ('token', 'secret', 'password', 'access_token', 'app_secret', 'page_token')
        raw = resolve_agent().get_accounts()
        safe = {}
        for aid, cfg in raw.items():
            safe[aid] = {k: ('***' if any(s in k.lower() for s in sensitive) else v) for k, v in cfg.items()}
        return jsonify({'accounts': safe, 'current': resolve_agent().meta_api.ad_account_id})

    @app.route('/api/accounts/save', methods=['POST'])
    @require_tenant_auth
    def api_save_account():
        data = request.get_json()
        aid = resolve_agent().save_account(data)
        if aid:
            return jsonify({'success': True, 'id': aid})
        return jsonify({'success': False, 'error': 'Could not save account'}), 400

    @app.route('/api/accounts/delete/<account_id>', methods=['POST'])
    @require_tenant_auth
    def api_delete_account(account_id):
        if resolve_agent().delete_account(account_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Account not found'}), 404

    @app.route('/api/reports')
    def api_reports():
        campaigns = resolve_agent().get_all_campaigns()
        total_spend = 0
        total_leads = 0
        total_clicks = 0
        total_impressions = 0
        for c in campaigns:
            metrics = ads_agent.performance_optimizer.get_campaign_metrics(c.get('campaign_id', ''))
            if metrics:
                total_spend += metrics.get('spend', 0)
                total_leads += metrics.get('leads', 0)
                total_clicks += metrics.get('clicks', 0)
                total_impressions += metrics.get('impressions', 0)
        return jsonify({
            'campaigns': len(campaigns),
            'total_spend': round(total_spend, 2),
            'total_leads': total_leads,
            'total_clicks': total_clicks,
            'total_impressions': total_impressions,
            'avg_cpc': round(total_spend / total_clicks, 2) if total_clicks else 0,
            'cost_per_lead': round(total_spend / total_leads, 2) if total_leads else 0,
            'ctr': round(total_clicks / total_impressions * 100, 2) if total_impressions else 0
        })

    @app.route('/api/pixel', methods=['GET', 'POST'])
    @require_tenant_auth
    def api_pixel():
        pixel_file = os.path.join(os.path.dirname(__file__), 'pixel_config.json')
        if request.method == 'POST':
            data = request.get_json()
            try:
                with open(pixel_file, 'w') as f:
                    json.dump(data, f)
                return jsonify({'success': True})
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)}), 400
        try:
            if os.path.exists(pixel_file):
                with open(pixel_file) as f:
                    return jsonify(json.load(f))
        except: pass
        return jsonify({'pixel_id': ''})

    @app.route('/api/posts')
    def api_posts():
        return jsonify({'posts': scheduler.get_all_posts()})

    @app.route('/api/posts/<post_id>')
    def api_post_detail(post_id):
        post = scheduler.get_post(post_id)
        if not post:
            return jsonify({'error': 'Not found'}), 404
        return jsonify({'post': post})

    @app.route('/api/posts/repeat', methods=['POST'])
    @require_tenant_auth
    def api_repeat_post():
        data = request.get_json()
        if not data or not data.get('source_id') or not data.get('times'):
            return jsonify({'success': False, 'error': 'Missing data'}), 400
        times = data['times']
        created = []
        for i, t in enumerate(times):
            post_data = {
                'platform': data.get('platform', 'facebook'),
                'content_type': data.get('content_type', 'image'),
                'headline': data.get('headline', ''),
                'message': data.get('message', ''),
                'ai_instruction': data.get('ai_instruction', ''),
                'cta': data.get('cta', ''),
                'link_url': data.get('link_url', ''),
                'media_file': '',
                'media_url': '',
                'media_urls': [],
                'media_files': [],
                'scheduled_time': t,
                'status': 'scheduled'
            }
            if i > 0:
                time.sleep(0.01)
            post = scheduler.create_post(post_data)
            created.append(post)
        return jsonify({'success': True, 'count': len(created), 'posts': created})

    @app.route('/api/posts/create', methods=['POST'])
    @require_tenant_auth
    def api_create_post():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data'}), 400
        platform = data.get('platform', 'facebook')
        message = data.get('message', '')
        if not message and not data.get('media_url'):
            return jsonify({'success': False, 'error': 'Message or media required'}), 400
        publish_now = data.get('status') == 'publish_now'
        post_data = {
            'platform': platform,
            'content_type': data.get('content_type', 'image'),
            'headline': data.get('headline', ''),
            'message': message,
            'ai_instruction': data.get('ai_instruction', ''),
            'cta': data.get('cta', ''),
            'media_file': data.get('media_file', ''),
            'media_url': data.get('media_url', ''),
            'media_urls': data.get('media_urls', []),
            'media_files': data.get('media_files', []),
            'link_url': data.get('link_url', ''),
            'scheduled_time': data.get('scheduled_time'),
            'status': 'draft' if not publish_now else 'draft'
        }
        if data.get('scheduled_time'):
            post_data['scheduled_time'] = data['scheduled_time']
            post_data['status'] = 'scheduled'
        post = scheduler.create_post(post_data)
        published = False
        if publish_now:
            result = scheduler.publish_now(post['id'])
            if 'error' not in result:
                published = True
            else:
                err = result['error']
                if isinstance(err, dict):
                    err = err.get('message', str(err))
                return jsonify({'success': False, 'error': err, 'post': post}), 400
        return jsonify({'success': True, 'post': post, 'published': published})

    @app.route('/api/posts/publish/<post_id>', methods=['POST'])
    @require_tenant_auth
    def api_publish_post(post_id):
        result = scheduler.publish_now(post_id)
        if 'error' in result:
            err = result['error']
            if isinstance(err, dict):
                err = err.get('message', str(err))
            return jsonify({'success': False, 'error': err}), 400
        return jsonify({'success': True, 'result': result})

    @app.route('/api/posts/delete/<post_id>', methods=['POST'])
    @require_tenant_auth
    def api_delete_post(post_id):
        if scheduler.delete_post(post_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Post not found'}), 404

    @app.route('/api/posts/restore/<post_id>', methods=['POST'])
    @require_tenant_auth
    def api_restore_post(post_id):
        if scheduler.restore_post(post_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Post not found'}), 404

    @app.route('/api/posts/delete-forever/<post_id>', methods=['POST'])
    @require_tenant_auth
    def api_delete_forever(post_id):
        if scheduler.delete_forever(post_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Post not found'}), 404

    # Auto Responder routes
    responder = SocialMediaAutoResponder(ads_agent.meta_api)

    @app.route('/api/responder/status')
    def api_responder_status():
        return jsonify({
            'enabled': responder.enabled,
            'rules': responder.rules,
            'use_ai': responder.rules.get('use_ai', True)
        })

    @app.route('/api/responder/toggle', methods=['POST'])
    @require_tenant_auth
    def api_responder_toggle():
        data = request.get_json()
        responder.enabled = data.get('enabled', True)
        return jsonify({'success': True})

    @app.route('/api/responder/rules')
    def api_responder_rules():
        return jsonify({'rules': responder.rules.get('rules', [])})

    @app.route('/api/responder/rules/add', methods=['POST'])
    @require_tenant_auth
    def api_responder_add_rule():
        data = request.get_json()
        rule = responder.add_rule(
            data.get('keyword', ''),
            data.get('response_template', ''),
            data.get('platform', 'all'),
            data.get('sentiment')
        )
        return jsonify({'success': True, 'rule': rule})

    @app.route('/api/responder/rules/delete/<rule_id>', methods=['POST'])
    @require_tenant_auth
    def api_responder_delete_rule(rule_id):
        responder.delete_rule(rule_id)
        return jsonify({'success': True})

    @app.route('/api/responder/default-response', methods=['POST'])
    @require_tenant_auth
    def api_responder_default():
        data = request.get_json()
        responder.set_default_response(data.get('text', ''))
        return jsonify({'success': True})

    @app.route('/api/responder/ai-mode', methods=['POST'])
    @require_tenant_auth
    def api_responder_ai_mode():
        data = request.get_json()
        responder.set_ai_mode(data.get('enabled', True))
        return jsonify({'success': True})

    @app.route('/api/responder/log')
    def api_responder_log():
        limit = request.args.get('limit', 50, type=int)
        return jsonify({'responses': responder.get_log(limit)})

    @app.route('/api/responder/scan', methods=['POST'])
    @require_tenant_auth
    def api_responder_scan():
        data = request.get_json()
        results = responder.auto_respond_all(data.get('page_id'), data.get('limit', 20))
        return jsonify({'success': True, 'results': results})

    # Multi-Platform Scheduler routes
    multi_scheduler = MultiPlatformScheduler(ads_agent.meta_api)
    multi_scheduler.start_auto_publish()

    @app.route('/api/multi-scheduler/queue')
    def api_multi_queue():
        return jsonify({'items': multi_scheduler.get_queue()})

    @app.route('/api/multi-scheduler/item/<item_id>')
    def api_multi_item(item_id):
        item = multi_scheduler.get_item(item_id)
        if not item:
            return jsonify({'error': 'Not found'}), 404
        return jsonify({'item': item})

    @app.route('/api/multi-scheduler/repeat', methods=['POST'])
    @require_tenant_auth
    def api_multi_repeat():
        data = request.get_json()
        if not data or not data.get('source_id') or not data.get('times'):
            return jsonify({'success': False, 'error': 'Missing data'}), 400
        times = data['times']
        source = multi_scheduler.get_item(data['source_id'])
        if not source:
            return jsonify({'success': False, 'error': 'Source not found'}), 404
        platforms = source.get('platforms', ['facebook'])
        created = []
        for i, t in enumerate(times):
            if i > 0:
                time.sleep(0.01)
            item = multi_scheduler.schedule_post(
                platforms,
                data.get('message', ''),
                scheduled_time=t,
                content_type=source.get('content_type', 'image'),
                link_url=data.get('link_url', ''),
                headline=data.get('headline', ''),
                ai_instruction=data.get('ai_instruction', ''),
                cta=data.get('cta', '')
            )
            created.append(item)
        return jsonify({'success': True, 'count': len(created), 'items': created})

    @app.route('/api/multi-scheduler/schedule', methods=['POST'])
    @require_tenant_auth
    def api_multi_schedule():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data'}), 400
        platforms = data.get('platforms', ['facebook'])
        message = data.get('message', '')
        media_urls = data.get('media_urls', [])
        scheduled_time = data.get('scheduled_time')
        content_type = data.get('content_type', 'image')
        link_url = data.get('link_url', '')
        media_file = data.get('media_file', '')
        media_files = data.get('media_files', [])
        headline = data.get('headline', '')
        ai_instruction = data.get('ai_instruction', '')
        cta = data.get('cta', '')
        item = multi_scheduler.schedule_post(platforms, message, media_urls, scheduled_time, content_type=content_type, link_url=link_url, media_file=media_file, media_files=media_files, headline=headline, ai_instruction=ai_instruction, cta=cta)
        return jsonify({'success': True, 'item': item})

    @app.route('/api/multi-scheduler/publish/<item_id>', methods=['POST'])
    @require_tenant_auth
    def api_multi_publish(item_id):
        results = multi_scheduler.publish_item(item_id)
        return jsonify({'success': True, 'results': results})

    @app.route('/api/multi-scheduler/publish-pending', methods=['POST'])
    @require_tenant_auth
    def api_multi_publish_pending():
        results = multi_scheduler.publish_pending()
        return jsonify({'success': True, 'results': results})

    @app.route('/api/multi-scheduler/delete/<item_id>', methods=['POST'])
    @require_tenant_auth
    def api_multi_delete(item_id):
        multi_scheduler.delete_item(item_id)
        return jsonify({'success': True})

    # Lead Management routes
    lead_mgmt = AdvancedLeadManagement(ads_agent.meta_api)

    @app.route('/api/leads-v2')
    def api_leads_v2():
        status = request.args.get('status')
        limit = request.args.get('limit', 100, type=int)
        return jsonify({'leads': lead_mgmt.get_leads(status, limit)})

    @app.route('/api/leads-v2/stats')
    def api_leads_v2_stats():
        return jsonify(lead_mgmt.get_lead_stats())

    @app.route('/api/kpi-panel')
    def api_kpi_panel():
        """The 3 numbers that actually answer 'is this working': cost per lead,
        % that book a trial class, % that enroll. Real ad spend comes straight
        from Meta's Insights API (not the daily_budget setting -- actual money
        spent). Optionally filter by date range and/or a specific campaign."""
        agent = resolve_agent()
        date_from = request.args.get('from')  # 'YYYY-MM-DD', optional
        date_to = request.args.get('to')
        campaign_id_filter = request.args.get('campaign_id')

        # 1) Real spend, straight from Meta -- sum across the relevant campaigns
        total_spend = 0.0
        campaigns_included = []
        for cid, c in agent.campaigns.items():
            if c.get('status') in ('TRASHED',):
                continue
            if campaign_id_filter and cid != campaign_id_filter:
                continue
            try:
                metrics = agent.performance_optimizer.get_campaign_metrics(cid)
                total_spend += metrics.get('spend', 0) or 0
                campaigns_included.append(cid)
            except Exception as e:
                print(f"[kpi-panel] could not fetch metrics for {cid}: {e}")

        # 2) Leads, filtered by date range if given (using imported_at / created_time)
        def in_range(lead):
            if not date_from and not date_to:
                return True
            ts = (lead.get('imported_at') or lead.get('created_time') or '')[:10]
            if date_from and ts < date_from:
                return False
            if date_to and ts > date_to:
                return False
            return True

        leads = [l for l in lead_mgmt.leads.get('leads', []) if in_range(l)]
        total_leads = len(leads)
        trial_booked = sum(1 for l in leads if l.get('status') in ('trial_booked', 'converted'))
        enrolled = sum(1 for l in leads if l.get('status') == 'converted')
        lost = sum(1 for l in leads if l.get('status') == 'lost')

        cost_per_lead = round(total_spend / total_leads, 2) if total_leads else None
        booking_rate = round(trial_booked / total_leads * 100, 1) if total_leads else None
        enrollment_rate = round(enrolled / total_leads * 100, 1) if total_leads else None
        close_rate_from_trial = round(enrolled / trial_booked * 100, 1) if trial_booked else None
        cost_per_enrollment = round(total_spend / enrolled, 2) if enrolled else None

        # Simple confidence flag -- so the number isn't mistaken for a verdict
        # before there's enough data to mean anything (per Meta's own guidance:
        # ~50 conversions/week is 'fully learned'; we flag well below that too).
        if total_leads == 0:
            confidence = 'sin_datos'
        elif total_leads < 10:
            confidence = 'muy_bajo'
        elif total_leads < 30:
            confidence = 'bajo'
        else:
            confidence = 'aceptable'

        return jsonify({
            'success': True,
            'tenant_id': getattr(agent, 'tenant_id', 'default'),
            'period': {'from': date_from, 'to': date_to},
            'campaigns_included': campaigns_included,
            'total_spend': round(total_spend, 2),
            'total_leads': total_leads,
            'cost_per_lead': cost_per_lead,
            'trial_booked_count': trial_booked,
            'booking_rate_pct': booking_rate,
            'enrolled_count': enrolled,
            'enrollment_rate_pct': enrollment_rate,
            'close_rate_from_trial_pct': close_rate_from_trial,
            'cost_per_enrollment': cost_per_enrollment,
            'lost_count': lost,
            'confidence': confidence,
            'confidence_note': {
                'sin_datos': 'No leads recorded for this period yet.',
                'muy_bajo': 'Fewer than 10 leads \u2014 just an initial sample, don\'t draw conclusions yet.',
                'bajo': 'Between 10 and 30 leads \u2014 starting to be useful, but wait for more before making big changes.',
                'aceptable': '30+ leads \u2014 enough to start trusting these numbers.'
            }[confidence]
        })

    @app.route('/api/leads-v2/fetch-meta', methods=['POST'])
    @require_tenant_auth
    def api_leads_v2_fetch():
        data = request.get_json() or {}
        ads = lead_mgmt.fetch_meta_leads(data.get('ad_id'), data.get('limit', 50))
        return jsonify({'success': True, 'leads_count': len(ads)})

    @app.route('/api/leads-v2/add', methods=['POST'])
    @require_tenant_auth
    def api_leads_v2_add():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data'}), 400
        lead = lead_mgmt.add_lead_manual(
            data.get('name', ''),
            data.get('email', ''),
            data.get('phone', ''),
            data.get('source', 'manual'),
            data.get('notes', ''),
            data.get('field_data', {})
        )
        return jsonify({'success': True, 'lead': lead})

    @app.route('/api/leads-v2/score/<lead_id>', methods=['POST'])
    @require_tenant_auth
    def api_leads_v2_score(lead_id):
        score = lead_mgmt.score_lead(lead_id)
        return jsonify({'success': True, 'score': score})

    @app.route('/api/leads-v2/status/<lead_id>', methods=['POST'])
    @require_tenant_auth
    def api_leads_v2_status(lead_id):
        data = request.get_json()
        lead = lead_mgmt.update_lead_status(lead_id, data.get('status', ''), data.get('notes'))
        if lead:
            return jsonify({'success': True, 'lead': lead})
        return jsonify({'success': False, 'error': 'Lead not found'}), 404

    @app.route('/api/leads-v2/delete/<lead_id>', methods=['POST'])
    @require_tenant_auth
    def api_leads_v2_delete(lead_id):
        lead_mgmt.delete_lead(lead_id)
        return jsonify({'success': True})

    @app.route('/api/leads-v2/workflows')
    def api_workflows():
        return jsonify({'workflows': lead_mgmt.workflows.get('workflows', [])})

    @app.route('/api/leads-v2/workflows/create', methods=['POST'])
    @require_tenant_auth
    def api_create_workflow():
        data = request.get_json()
        wf = lead_mgmt.create_workflow(data.get('name', ''), data.get('steps', []))
        return jsonify({'success': True, 'workflow': wf})

    @app.route('/api/leads-v2/workflows/delete/<workflow_id>', methods=['POST'])
    @require_tenant_auth
    def api_delete_workflow(workflow_id):
        lead_mgmt.delete_workflow(workflow_id)
        return jsonify({'success': True})

    @app.route('/api/leads-v2/assign-workflow', methods=['POST'])
    @require_tenant_auth
    def api_assign_workflow():
        data = request.get_json()
        lead = lead_mgmt.assign_workflow(data.get('lead_id'), data.get('workflow_id'))
        if lead:
            return jsonify({'success': True, 'lead': lead})
        return jsonify({'success': False, 'error': 'Lead not found'}), 404

    @app.route('/api/leads-v2/run-workflows', methods=['POST'])
    @require_tenant_auth
    def api_run_workflows():
        results = lead_mgmt.run_workflows()
        return jsonify({'success': True, 'results': results})

    return app


# ==================== MAIN ====================

def main():
    print("Meta Ads Agent Starting Meta Ads Agent...")

    try:
        from dotenv import load_dotenv
        load_dotenv('.env.txt')
        print("[OK] .env loaded")
    except ImportError:
        print("[!] python-dotenv not installed - using environment variables directly")

    meta_credentials = {
        'access_token': os.environ.get('META_ACCESS_TOKEN', 'YOUR_META_ACCESS_TOKEN'),
        'ad_account_id': os.environ.get('META_AD_ACCOUNT_ID', 'act_YOUR_ACCOUNT_ID'),
        'app_id': os.environ.get('META_APP_ID'),
        'app_secret': os.environ.get('META_APP_SECRET'),
        'page_token': os.environ.get('META_PAGE_TOKEN')
    }

    ai_api_key = os.environ.get('OPENAI_API_KEY')
    ai_api_key = os.environ.get('GROQ_API_KEY') or os.environ.get('GEMINI_API_KEY')
    ai_provider = 'groq' if os.environ.get('GROQ_API_KEY') else 'gemini'

    global agent
    agent = UniversalMetaAdsAgent(meta_credentials, ai_api_key)
    agent.ai_api_key = ai_api_key
    agent.ai_provider = ai_provider

    # Page token loaded from META_PAGE_TOKEN (long-lived, has pages_manage_posts)

    app = create_web_interface(agent, tenant_manager=TenantManager(default_agent=agent))

    print("[OK] Agent initialized!")
    print("[WEB] Dashboard: http://localhost:5000")
    print("[CTRL+C] to stop\n")

    def auto_optimize_loop():
        while True:
            time.sleep(3600)
            try:
                agent.optimize_campaigns()
                print(f"[AUTO] Optimization check at {time.strftime('%H:%M')}")
            except: pass

    def token_expiry_check():
        import smtplib
        from email.mime.text import MIMEText
        smtp_host = os.environ.get('SMTP_HOST')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        smtp_user = os.environ.get('SMTP_USER')
        smtp_pass = os.environ.get('SMTP_PASS')
        alert_email = os.environ.get('ALERT_EMAIL')
        if not all([smtp_host, smtp_user, smtp_pass, alert_email]):
            return
        warned = False
        while True:
            time.sleep(86400)
            try:
                expires_at = getattr(agent.meta_api, 'token_expires_at', None)
                if expires_at and not warned:
                    remaining = expires_at - time.time()
                    if remaining < 7 * 86400:
                        msg = MIMEText(
                            f"Your Meta Access Token expires in {int(remaining/86400)} days.\n"
                            f"Please refresh it in the dashboard Settings page."
                        )
                        msg['Subject'] = '[Meta Ads] Token Expiring Soon'
                        msg['From'] = smtp_user
                        msg['To'] = alert_email
                        with smtplib.SMTP(smtp_host, smtp_port) as s:
                            s.starttls()
                            s.login(smtp_user, smtp_pass)
                            s.send_message(msg)
                        warned = True
                        print(f"[ALERT] Token expiry email sent to {alert_email}")
                    elif remaining < 0:
                        warned = False
            except Exception as e:
                print(f"[WARN] Token check failed: {e}")

    t = threading.Thread(target=auto_optimize_loop, daemon=True)
    t.start()
    te = threading.Thread(target=token_expiry_check, daemon=True)
    te.start()

    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))

    if os.environ.get('PRODUCTION'):
        try:
            from waitress import serve
            print(f"Starting production server on {host}:{port}...")
            serve(app, host=host, port=port, threads=4)
        except ImportError:
            print("waitress not installed, falling back to Flask dev server")
            app.run(host=host, port=port, debug=False)
    else:
        app.run(host=host, port=port, debug=False)

if __name__ == '__main__':
    main()
