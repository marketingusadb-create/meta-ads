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
from flask import Flask, send_from_directory, request, jsonify

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
            "optimization_goal": ad_set_config.get('optimization_goal', 'LINK_CLICKS'),
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
        if self.app_id and self.app_secret:
            url = f"{self.base_url}/oauth/access_token"
            params = {
                'client_id': self.app_id,
                'client_secret': self.app_secret,
                'grant_type': 'client_credentials'
            }
            try:
                response = self.session.get(url, params=params)
                if response.status_code == 200:
                    self.access_token = response.json().get('access_token')
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
        full_path = os.path.join(os.path.dirname(__file__), image_path.lstrip('/'))
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
        full_path = os.path.join(os.path.dirname(__file__), video_path.lstrip('/'))
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
        self.audience_insights = {
            'restaurant': {
                'primary_interests': ['food', 'dining', 'cooking', 'restaurants'],
                'secondary_interests': ['travel', 'entertainment', 'socializing'],
                'demographic_targeting': {
                    'age_groups': ['25-44', '45-64'],
                    'income_levels': ['medium', 'high'],
                    'locations': ['urban', 'suburban']
                }
            },
            'retail': {
                'primary_interests': ['shopping', 'fashion', 'deals', 'products'],
                'secondary_interests': ['travel', 'entertainment', 'home improvement'],
                'demographic_targeting': {
                    'age_groups': ['18-34', '35-54'],
                    'income_levels': ['low', 'medium', 'high'],
                    'locations': ['urban', 'suburban', 'rural']
                }
            },
            'service': {
                'primary_interests': ['professional services', 'expertise', 'advice'],
                'secondary_interests': ['finance', 'insurance', 'real estate'],
                'demographic_targeting': {
                    'age_groups': ['35-54', '55+'],
                    'income_levels': ['medium', 'high'],
                    'locations': ['urban', 'suburban']
                }
            },
            'ecommerce': {
                'primary_interests': ['online shopping', 'deals', 'reviews', 'products'],
                'secondary_interests': ['technology', 'finance', 'travel'],
                'demographic_targeting': {
                    'age_groups': ['18-34', '35-54'],
                    'income_levels': ['low', 'medium', 'high'],
                    'locations': ['urban', 'suburban']
                }
            }
        }

    def identify_target_audience(self, business_profile):
        industry = business_profile.get('industry', 'service')
        location = business_profile.get('location', '')
        insights = self.audience_insights.get(industry, self.audience_insights['service'])
        return {
            'primary_audience': insights['primary_interests'],
            'secondary_audience': insights['secondary_interests'],
            'geographic_targeting': self.get_geographic_targeting(location),
            'interest_targeting': insights['primary_interests'],
            'behavioral_targeting': ['mobile shopper', 'price sensitive', 'brand loyal']
        }

    def get_geographic_targeting(self, location):
        return {
            'primary_location': location,
            'radius': 25,
            'countries': ['US'],
        }

    def get_interest_targeting(self, industry):
        insights = self.audience_insights.get(industry, self.audience_insights['service'])
        return insights['primary_interests']

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
        insights_db = {
            'restaurant': {
                'industry': 'restaurant',
                'primary_offering': 'Food and Dining',
                'peak_hours': 'lunch and dinner',
                'target_demographics': 'families and young professionals',
                'conversion_factors': ['ambiance', 'price', 'location', 'reviews']
            },
            'retail': {
                'industry': 'retail',
                'primary_offering': 'Retail Products',
                'peak_hours': 'weekends and evenings',
                'target_demographics': 'all age groups',
                'conversion_factors': ['price', 'quality', 'convenience', 'brand']
            },
            'service': {
                'industry': 'service',
                'primary_offering': 'Professional Services',
                'peak_hours': 'business hours',
                'target_demographics': 'adults and businesses',
                'conversion_factors': ['reputation', 'expertise', 'cost', 'convenience']
            },
            'ecommerce': {
                'industry': 'ecommerce',
                'primary_offering': 'E-commerce',
                'peak_hours': 'weekends and holidays',
                'target_demographics': 'tech-savvy shoppers',
                'conversion_factors': ['price', 'reviews', 'shipping', 'return policy']
            },
            'martialarts': {
                'industry': 'martialarts',
                'primary_offering': 'classes',
                'peak_hours': 'afternoons and weekends',
                'target_demographics': 'adults and children',
                'conversion_factors': ['instructor quality', 'location', 'pricing', 'community']
            },
            'medical': {
                'industry': 'medical',
                'primary_offering': 'services',
                'peak_hours': 'business hours',
                'target_demographics': 'adults and families',
                'conversion_factors': ['credentials', 'location', 'insurance', 'reviews']
            }
        }
        return insights_db.get(industry, insights_db['service'])

    def recommend_budget(self, business_profile):
        industry = business_profile.get('industry', 'service')
        budgets = {
            'restaurant': {'facebook': 40, 'instagram': 30, 'messenger': 20, 'whatsapp': 10},
            'retail': {'facebook_feed': 50, 'instagram_feed': 30, 'facebook_marketplace': 20},
            'ecommerce': {'facebook': 35, 'instagram': 35, 'google_ads': 30},
            'service': {'facebook': 50, 'instagram': 30, 'messenger': 20},
            'martialarts': {'facebook': 40, 'instagram': 35, 'messenger': 25},
            'medical': {'facebook': 50, 'instagram': 25, 'messenger': 25}
        }
        return budgets.get(industry, budgets['service'])

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
        objectives_db = {
            'restaurant': ['OUTCOME_LEADS', 'OUTCOME_TRAFFIC', 'OUTCOME_SALES'],
            'retail': ['OUTCOME_SALES', 'OUTCOME_LEADS', 'OUTCOME_AWARENESS'],
            'service': ['OUTCOME_LEADS', 'OUTCOME_TRAFFIC', 'OUTCOME_SALES'],
            'ecommerce': ['OUTCOME_SALES', 'OUTCOME_TRAFFIC', 'OUTCOME_AWARENESS']
        }
        return objectives_db.get(industry_insights.get('industry', 'service'), objectives_db['service'])

    def select_ad_types(self, industry_insights):
        ad_types_db = {
            'restaurant': ['image carousel', 'video ads', 'collection'],
            'retail': ['carousel ads', 'video ads', 'story ads'],
            'service': ['image ads', 'text ads', 'video ads'],
            'ecommerce': ['product carousel', 'video ads', 'collection ads']
        }
        return ad_types_db.get(industry_insights.get('industry', 'service'), ad_types_db['service'])

    def estimate_roas(self, industry_insights):
        roas_db = {'restaurant': 3.5, 'retail': 4.2, 'service': 5.1, 'ecommerce': 6.8}
        return roas_db.get(industry_insights.get('industry', 'service'), 4.0)

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
    def __init__(self, meta_api=None):
        self.meta_api = meta_api
        self.monitoring_active = False

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

    def analyze_budget_efficiency(self, metrics):
        return {
            'roi': metrics.get('roas', 0),
            'efficiency_score': round(metrics.get('roas', 0) / 4.0, 2)
        }

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
                    new_budget = min(current_budget * 1.2, 500)
                    result = self.meta_api.update_campaign(campaign_id, daily_budget=int(new_budget * 100))
                    results.append(f"Budget increased: ${current_budget:.0f} → ${new_budget:.0f}/day")
                    print(f"Action: increase_budget -> ${new_budget:.0f}/day")

                elif opt == 'adjust_budget_allocation' and current_budget:
                    grade = getattr(self, '_last_grade', 'Average')
                    factor = 1.3 if grade in ('Excellent', 'Good') else (0.8 if grade == 'Poor' else 1.0)
                    new_budget = min(current_budget * factor, 500)
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
    def __init__(self, meta_credentials, ai_api_key=None):
        self.meta_api = MetaAPI(
            meta_credentials['access_token'],
            meta_credentials['ad_account_id'],
            meta_credentials.get('app_id'),
            meta_credentials.get('app_secret'),
            page_token=meta_credentials.get('page_token')
        )
        self.ai_strategy_engine = AIStrategyEngine(ai_api_key)
        self.performance_optimizer = PerformanceOptimizer(self.meta_api)
        self.audience_analyzer = AudienceAnalyzer()
        self.campaigns_file = os.path.join(os.path.dirname(__file__), 'campaigns.json')
        self.campaigns = self._load_campaigns()
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
            'optimization_goal': 'LINK_CLICKS',
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
        post_id = f"post_{int(time.time())}"
        post = {
            'id': post_id,
            'platform': data.get('platform', 'facebook'),
            'content_type': data.get('content_type', 'image'),
            'message': data.get('message', ''),
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
            del self.posts[post_id]
            self._save_posts()
            return True
        return False

    def get_all_posts(self):
        return list(self.posts.values())

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
    <div style="display:flex;gap:8px;">
      <input type="text" id="pixel-id" data-i18n-placeholder="pixel_id" placeholder="Pixel ID" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;">
      <button class="btn btn-primary btn-sm" onclick="savePixel()" data-i18n="save_pixel">Save Pixel</button>
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
      <select id="cp-platform" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;" onchange="toggleCpMedia()">
        <option value="facebook">Facebook</option>
        <option value="instagram">Instagram</option>
      </select>
    </div>
    <div class="form-group">
      <label data-i18n="message">Message</label>
      <textarea id="cp-message" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;min-height:80px;" data-i18n-placeholder="write_post" placeholder="Write your post..."></textarea>
    </div>
    <div class="form-group" id="cp-link-group">
      <label data-i18n="dest_url">Link URL (optional)</label>
      <input type="url" id="cp-link" placeholder="https://..." style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
    </div>
    <div class="form-group">
      <label data-i18n="type">Content Type</label>
      <select id="cp-content-type" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;" onchange="toggleCpMedia()">
        <option value="image">Image</option>
        <option value="carousel">Carousel (multiple images)</option>
        <option value="video">Video</option>
      </select>
    </div>
    <div class="form-group">
      <label data-i18n="upload_media">Media</label>
      <input type="file" id="cp-media" accept="image/*" style="width:100%;padding:8px;">
      <div style="font-size:.75rem;margin-top:4px;color:#65676b;" data-i18n="media_hint">Select multiple images for carousel, or one video</div>
      <div id="cp-media-preview" style="margin-top:8px;display:none;"><img id="cp-preview-img" style="max-width:200px;max-height:200px;border-radius:8px;"></div>
      <div id="cp-carousel-thumbs" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;display:none;"></div>
      <video id="cp-preview-video" style="max-height:160px;border-radius:6px;display:none;margin-top:8px;" controls></video>
    </div>
    <div class="form-group">
      <label data-i18n="schedule_label">Schedule (optional — leave empty for draft)</label>
      <input type="datetime-local" id="cp-schedule" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
    </div>
    <div class="form-group" style="display:flex;gap:8px;flex-wrap:wrap;">
      <button class="btn btn-primary" onclick="savePostAsDraft()" style="flex:1;" data-i18n="save_draft">Save as Draft</button>
      <button class="btn btn-success" onclick="schedulePost()" style="flex:1;" data-i18n="schedule_post">Schedule</button>
      <button class="btn btn-warn" onclick="publishPostNow()" style="flex:1;" data-i18n="publish_post">Publish Now</button>
    </div>
    <div id="cp-status" style="margin-top:12px;font-size:.85rem;text-align:center;"></div>
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
        <div style="font-size:11px;color:#888;margin-top:2px">Describe lo que quieres promocionar. La IA usar\u00e1 esto para generar headlines y textos \u00fanicos.</div>
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
        <label data-i18n="platforms">Plataformas</label>
        <select id="f-platforms" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;" onchange="toggleBudgetSplit()">
          <option value="both" data-i18n="both">Facebook + Instagram (ambos)</option>
          <option value="facebook" data-i18n="facebook">Solo Facebook</option>
          <option value="instagram" data-i18n="instagram">Solo Instagram</option>
        </select>
      </div>
      <div class="form-group" id="budget-split-group" style="display:none;">
        <label data-i18n="budget_split">Reparto del presupuesto</label>
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
        <label data-i18n="placements">Ubicaciones (placements)</label>
        <div style="display:flex;flex-wrap:wrap;gap:8px;padding:8px;border:1px solid #ddd;border-radius:6px;background:#f9f9f9;">
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="feed" checked onchange="updatePlacements()"> <span data-i18n="feed">Feed</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="story" checked onchange="updatePlacements()"> <span data-i18n="stories">Stories</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="marketplace" onchange="updatePlacements()"> <span data-i18n="marketplace">Marketplace</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;" class="ig-only"><input type="checkbox" value="reels" onchange="updatePlacements()"> <span data-i18n="reels">Reels</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;" class="ig-only"><input type="checkbox" value="explore" onchange="updatePlacements()"> <span data-i18n="explore">Explore</span></label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:400;"><input type="checkbox" value="video_feeds" onchange="updatePlacements()"> <span data-i18n="video_feed">Video Feed</span></label>
        </div>
        <div style="font-size:11px;color:#888;margin-top:4px">Selecciona d\u00f3nde aparecer\u00e1n tus anuncios</div>
      </div>
      <div class="form-group">
        <label data-i18n="gender">Genero</label>
        <select id="f-gender" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
          <option value="all" data-i18n="all">Todos</option>
          <option value="male" data-i18n="male">Hombres</option>
          <option value="female" data-i18n="female">Mujeres</option>
        </select>
      </div>
      <div class="form-group">
        <label data-i18n="interests">Intereses</label>
        <div id="interest-presets" style="display:flex;flex-wrap:wrap;gap:6px;padding:8px;border:1px solid #ddd;border-radius:6px;background:#f9f9f9;margin-bottom:8px;"></div>
        <div style="display:flex;gap:6px;margin-bottom:6px;">
          <input type="text" id="f-custom-interest" data-i18n-placeholder="custom_interest_placeholder" placeholder="Nombre del inter\u00e9s o ID de Facebook" style="flex:1;padding:6px;border:1px solid #ddd;border-radius:6px;font-size:13px;">
          <button class="btn btn-sm btn-primary" onclick="addCustomInterest()" data-i18n="add_interest">Agregar</button>
        </div>
        <div id="interest-tags" style="display:flex;flex-wrap:wrap;gap:4px;"></div>
        <div style="font-size:11px;color:#888;margin-top:4px" data-i18n="interest_helper">Selecciona intereses predefinidos o agrega los tuyos (ID num\u00e9rico de Facebook Ads)</div>
      </div>
      <div class="form-group">
        <label data-i18n="has_children">Tiene hijos</label>
        <select id="f-has-children" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;">
          <option value="" data-i18n="no_filter">No filtrar</option>
          <option value="yes" data-i18n="yes">Si</option>
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
            <div class="page-name" id="preview-page-name">Tu Negocio</div>
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
          <strong>IMPORTANTE:</strong> Al crear la campaña, Meta la crea en <strong>PAUSED</strong>. 
          Si ya tienes tarjeta configurada, revisa <strong>inmediatamente</strong> en Meta Ads Manager 
          que Campaign, Ad Set y Ad estén en <strong>OFF</strong> (gris). 
          Meta puede reactivarlos solo al agregar tarjeta.
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
var lang = localStorage.getItem('meta_ads_lang') || 'en';
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
  }
};
function _t(key) { return langData[lang] && langData[lang][key] !== undefined ? langData[lang][key] : langData.en[key] || key; }
function toggleLang() {
  lang = lang === 'en' ? 'es' : 'en';
  localStorage.setItem('meta_ads_lang', lang);
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
  document.getElementById('interest-tags').innerHTML = html || '<span style="font-size:12px;color:#888;">Ning\u00fan inter\u00e9s seleccionado</span>';
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
  const bizName = document.getElementById('f-name').value || 'Tu Negocio';

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
      var plHtml = '<div style="margin-top:8px;font-size:.8rem;color:#65676b;">Ubicaciones: ' + pl.map(function(v) { return plNames[v]||v; }).join(', ') + '</div>';
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
  if (name === 'settings') { loadAccounts(); loadPixel(); }
  if (name === 'content') loadPosts();
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
        el.innerHTML = 'Token: Ilimitado';
      } else if (days < 7) {
        el.innerHTML = 'Token expira en ' + days + ' dias - RENUEVA';
        el.style.color = '#ffcc00';
        el.style.fontWeight = 'bold';
      } else if (days < 30) {
        el.innerHTML = 'Token: ' + days + ' dias - Renueva pronto';
        el.style.color = '#ffcc00';
      } else {
        el.innerHTML = 'Token: ' + days + ' dias';
      }
    } else {
      el.innerHTML = 'Token: Ilimitado';
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
    var actId = (localStorage.getItem('meta_ad_account') || '1392277821202782').replace('act_','');
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
    document.getElementById('cp-platform').value = 'facebook';
    document.getElementById('cp-content-type').value = 'image';
    document.getElementById('cp-message').value = '';
    document.getElementById('cp-link').value = '';
    document.getElementById('cp-schedule').value = '';
    document.getElementById('cp-media').value = '';
    document.getElementById('cp-media').accept = 'image/*';
    document.getElementById('cp-media').multiple = false;
    document.getElementById('cp-media-preview').style.display = 'none';
    document.getElementById('cp-carousel-thumbs').style.display = 'none';
    document.getElementById('cp-carousel-thumbs').innerHTML = '';
    document.getElementById('cp-preview-video').style.display = 'none';
    document.getElementById('cp-preview-video').src = '';
    document.getElementById('cp-status').innerHTML = '';
    document.getElementById('create-post-modal').classList.add('open');
    toggleCpMedia();
  }

  function closeCreatePostModal() {
    document.getElementById('create-post-modal').classList.remove('open');
  }

  function toggleCpMedia() {
    var platform = document.getElementById('cp-platform').value;
    var linkGroup = document.getElementById('cp-link-group');
    if (platform === 'instagram') {
      linkGroup.style.display = 'none';
    } else {
      linkGroup.style.display = '';
    }
    var ct = document.getElementById('cp-content-type').value;
    var input = document.getElementById('cp-media');
    if (ct === 'video') {
      input.accept = 'video/*';
      input.multiple = false;
    } else if (ct === 'carousel') {
      input.accept = 'image/*';
      input.multiple = true;
    } else {
      input.accept = 'image/*';
      input.multiple = false;
    }
    input.value = '';
    document.getElementById('cp-media-preview').style.display = 'none';
    document.getElementById('cp-carousel-thumbs').style.display = 'none';
    document.getElementById('cp-carousel-thumbs').innerHTML = '';
    document.getElementById('cp-preview-video').style.display = 'none';
    document.getElementById('cp-preview-video').src = '';
  }

  var _cpUploadedMedia = '';
  var _cpCarouselUrls = [];
  document.addEventListener('change', function(e) {
    if (e.target && e.target.id === 'cp-media') {
      var files = e.target.files;
      if (!files || files.length === 0) return;
      var ct = document.getElementById('cp-content-type').value;
      var statusEl = document.getElementById('cp-status');
      if (ct === 'carousel') {
        _cpUploadedMedia = '';
        _cpCarouselUrls = [];
        var uploadNext = function(i) {
          if (i >= files.length) {
            document.getElementById('cp-carousel-thumbs').style.display = 'flex';
            document.getElementById('cp-carousel-thumbs').innerHTML = _cpCarouselUrls.map(function(u, idx) {
              return '<div style="position:relative;display:inline-block;"><img src="' + u + '" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:2px solid #ddd;"><button onclick="removeCarouselItem(' + idx + ')" style="position:absolute;top:-6px;right:-6px;background:#e74c3c;color:#fff;border:none;border-radius:50%;width:18px;height:18px;font-size:12px;cursor:pointer;line-height:18px;">x</button></div>';
            }).join('');
            statusEl.innerHTML = _cpCarouselUrls.length + ' ' + _t('images_uploaded');
            return;
          }
          var fd = new FormData();
          fd.append('media', files[i]);
          fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
            if (d.success) {
              _cpCarouselUrls.push(d.url);
              statusEl.innerHTML = _t('uploaded') + ' ' + (_cpCarouselUrls.length) + '/' + files.length;
            } else {
              statusEl.innerHTML = _t('upload_failed') + ' ' + (i+1);
            }
            uploadNext(i+1);
          }).catch(function() { statusEl.innerHTML = _t('upload_error_at') + ' ' + (i+1); uploadNext(i+1); });
        };
        uploadNext(0);
      } else if (ct === 'video') {
        _cpCarouselUrls = [];
        var file = files[0];
        var fd = new FormData();
        fd.append('media', file);
        statusEl.innerHTML = _t('uploading_video');
        fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
          if (d.success) {
            _cpUploadedMedia = d.url;
            document.getElementById('cp-preview-video').src = d.url;
            document.getElementById('cp-preview-video').style.display = 'block';
            statusEl.innerHTML = _t('video_uploaded');
          } else {
            statusEl.innerHTML = _t('upload_failed');
          }
        }).catch(function() { statusEl.innerHTML = _t('upload_error'); });
      } else {
        _cpCarouselUrls = [];
        var file = files[0];
        var fd = new FormData();
        fd.append('media', file);
        statusEl.innerHTML = _t('uploading');
        fetch('/api/upload-media', {method:'POST', body:fd}).then(function(r) { return r.json(); }).then(function(d) {
          if (d.success) {
            _cpUploadedMedia = d.url;
            document.getElementById('cp-media-preview').style.display = '';
            document.getElementById('cp-preview-img').src = d.url;
            statusEl.innerHTML = _t('image_uploaded');
          } else {
            statusEl.innerHTML = _t('upload_failed');
          }
        }).catch(function() { statusEl.innerHTML = _t('upload_error'); });
      }
    }
  });

  function removeCarouselItem(index) {
    _cpCarouselUrls.splice(index, 1);
    var thumbsDiv = document.getElementById('cp-carousel-thumbs');
    thumbsDiv.innerHTML = _cpCarouselUrls.map(function(u, idx) {
      return '<div style="position:relative;display:inline-block;"><img src="' + u + '" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:2px solid #ddd;"><button onclick="removeCarouselItem(' + idx + ')" style="position:absolute;top:-6px;right:-6px;background:#e74c3c;color:#fff;border:none;border-radius:50%;width:18px;height:18px;font-size:12px;cursor:pointer;line-height:18px;">x</button></div>';
    }).join('');
    var statusEl = document.getElementById('cp-status');
    statusEl.innerHTML = _cpCarouselUrls.length + ' ' + _t('images_selected');
    if (_cpCarouselUrls.length === 0) {
      thumbsDiv.style.display = 'none';
    }
  }

  function getCpPayload() {
    var ct = document.getElementById('cp-content-type').value;
    var payload = {
      platform: document.getElementById('cp-platform').value,
      content_type: ct,
      message: document.getElementById('cp-message').value.trim(),
      link_url: document.getElementById('cp-link').value.trim(),
      media_url: ct === 'carousel' ? '' : _cpUploadedMedia,
      media_file: ct === 'carousel' ? '' : _cpUploadedMedia,
      media_urls: ct === 'carousel' ? _cpCarouselUrls : [],
      media_files: ct === 'carousel' ? _cpCarouselUrls : []
    };
    return payload;
  }

  function savePostAsDraft() {
    var payload = getCpPayload();
    payload.status = 'draft';
    payload.scheduled_time = null;
    submitPost(payload, _t('draft_saved'));
  }

  function schedulePost() {
    var sched = document.getElementById('cp-schedule').value;
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
    var statusEl = document.getElementById('cp-status');
    statusEl.innerHTML = _t('processing');
    fetch('/api/posts/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.success) {
          statusEl.innerHTML = successMsg;
          if (d.published) {
            showToast(_t('post_published'));
          }
          setTimeout(function() {
            closeCreatePostModal();
            loadPosts();
          }, 1000);
        } else {
          statusEl.innerHTML = _t('error') + ': ' + (d.error || 'Unknown');
        }
      }).catch(function(e) {
        statusEl.innerHTML = 'Error: ' + e.message;
      });
  }

  function publishPost(postId) {
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
      var modal = document.createElement('div');
      modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';
      var statuses = {published:_t('published'),scheduled:_t('scheduled'),draft:_t('drafts'),trashed:_t('trash')};
      function fmtDate(d) { if (!d) return '-'; var dt = new Date(d); return dt.toLocaleDateString() + ' ' + dt.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); }
      modal.innerHTML = '<div style="background:#fff;border-radius:12px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;padding:24px;box-shadow:0 8px 32px rgba(0,0,0,0.2);">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">' +
        '<h3 style="margin:0;font-size:18px;">📄 ' + _t('details') + '</h3>' +
        '<button onclick="this.parentElement.parentElement.parentElement.remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:#65676b;">&times;</button>' +
        '</div>' +
        '<table style="width:100%;border-collapse:collapse;">' +
        '<tr><td style="padding:8px 12px;font-weight:700;color:#65676b;border-bottom:1px solid #e4e6eb;width:120px;">' + _t('platform') + '</td><td style="padding:8px 12px;border-bottom:1px solid #e4e6eb;">' + (p.platform === 'facebook' ? _t('facebook') : _t('instagram')) + '</td></tr>' +
        '<tr><td style="padding:8px 12px;font-weight:700;color:#65676b;border-bottom:1px solid #e4e6eb;">' + _t('type') + '</td><td style="padding:8px 12px;border-bottom:1px solid #e4e6eb;">' + (p.content_type === 'image' ? _t('image') : p.content_type === 'video' ? _t('video') : p.content_type === 'carousel' ? _t('carousel') : p.content_type) + '</td></tr>' +
        '<tr><td style="padding:8px 12px;font-weight:700;color:#65676b;border-bottom:1px solid #e4e6eb;">' + _t('status') + '</td><td style="padding:8px 12px;border-bottom:1px solid #e4e6eb;">' + (statuses[p.status] || p.status) + '</td></tr>' +
        (p.published_at ? '<tr><td style="padding:8px 12px;font-weight:700;color:#65676b;border-bottom:1px solid #e4e6eb;">' + _t('published') + '</td><td style="padding:8px 12px;border-bottom:1px solid #e4e6eb;">' + fmtDate(p.published_at) + '</td></tr>' : '') +
        (p.scheduled_time ? '<tr><td style="padding:8px 12px;font-weight:700;color:#65676b;border-bottom:1px solid #e4e6eb;">' + _t('scheduled') + '</td><td style="padding:8px 12px;border-bottom:1px solid #e4e6eb;">' + fmtDate(p.scheduled_time) + '</td></tr>' : '') +
        '<tr><td style="padding:8px 12px;font-weight:700;color:#65676b;border-bottom:1px solid #e4e6eb;">' + _t('created') + '</td><td style="padding:8px 12px;border-bottom:1px solid #e4e6eb;">' + fmtDate(p.created_at) + '</td></tr>' +
        '</table>' +
        '<div style="margin-top:16px;"><strong>' + _t('message') + ':</strong></div>' +
        '<div style="background:#f0f2f5;border-radius:8px;padding:12px;margin-top:8px;white-space:pre-wrap;font-size:14px;line-height:1.5;">' + _esc(p.message || '(' + _t('no_message') + ')') + '</div>' +
        '</div>';
      document.body.appendChild(modal);
      modal.addEventListener('click', function(e) { if (e.target === modal) modal.remove(); });
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
</body>
</html>
"""

def create_web_interface(ads_agent):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'meta-ads-secret-key-2025')

    scheduler = ContentScheduler(ads_agent.meta_api)

    @app.route('/')
    def home():
        return HTML_TEMPLATE

    @app.route('/api/campaigns')
    def api_campaigns():
        return jsonify(ads_agent.get_all_campaigns())

    @app.route('/api/campaign/<campaign_id>/performance')
    def api_campaign_performance(campaign_id):
        perf = ads_agent.get_campaign_performance(campaign_id)
        if not perf:
            return jsonify({'error': 'Campaign not found'}), 404
        return jsonify(perf)

    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    @app.route('/uploads/<filename>')
    def serve_upload(filename):
        return send_from_directory(UPLOAD_FOLDER, filename)

    @app.route('/api/upload-media', methods=['POST'])
    def api_upload_media():
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
        filename = f"{int(time.time())}_{file.filename}"
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
    def api_create_campaign():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data received'})
        try:
            result = ads_agent.create_campaign_for_business(data)
            return jsonify({'success': True, 'result': result})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/delete-campaign/<campaign_id>', methods=['POST'])
    def api_delete_campaign(campaign_id):
        try:
            if campaign_id in ads_agent.campaigns:
                ads_agent.campaigns[campaign_id]['status'] = 'TRASHED'
                ads_agent._save_campaigns()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/restore-campaign/<campaign_id>', methods=['POST'])
    def api_restore_campaign(campaign_id):
        try:
            if campaign_id in ads_agent.campaigns:
                ads_agent.campaigns[campaign_id]['status'] = 'ARCHIVED'
                ads_agent._save_campaigns()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/delete-campaign-forever/<campaign_id>', methods=['POST'])
    def api_delete_campaign_forever(campaign_id):
        try:
            if campaign_id in ads_agent.campaigns:
                del ads_agent.campaigns[campaign_id]
                ads_agent._save_campaigns()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/optimize/<campaign_id>', methods=['GET', 'POST'])
    def api_optimize(campaign_id):
        perf = ads_agent.get_campaign_performance(campaign_id)
        if not perf:
            return jsonify({'success': False, 'error': 'Campaign not found'})
        opts = ads_agent.performance_optimizer.generate_optimizations(perf['analysis'])
        if request.method == 'GET':
            return jsonify({'success': True, 'optimizations': opts, 'preview': True})
        results = ads_agent.performance_optimizer.apply_optimizations(opts, campaign_id)
        return jsonify({'success': True, 'optimizations': opts, 'results': results})

    @app.route('/api/optimize-all', methods=['POST'])
    def api_optimize_all():
        ads_agent.optimize_campaigns()
        return jsonify({'success': True})

    @app.route('/api/token-status')
    def api_token_status():
        expires_at = getattr(ads_agent.meta_api, 'token_expires_at', None)
        return jsonify({'expires_at': expires_at})

    @app.route('/api/generate-ad-copy', methods=['POST'])
    def api_generate_ad_copy():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data'})
        language = data.get('language', 'en')
        data['ai_api_key'] = getattr(ads_agent, 'ai_api_key', '')
        data['ai_provider'] = getattr(ads_agent, 'ai_provider', 'groq')
        engine = AIStrategyEngine()
        engine.ai_api_key = data['ai_api_key']
        engine.ai_provider = data['ai_provider']
        result = engine.generate_ad_copy(data, language)
        return jsonify({'success': True, 'headlines': result['headlines'], 'messages': result['messages']})

    @app.route('/api/lead-forms')
    def api_lead_forms():
        page_id = getattr(ads_agent.meta_api, 'page_id', '117339024950778')
        forms = ads_agent.meta_api.get_lead_forms(page_id)
        return jsonify({'success': True, 'forms': forms})

    @app.route('/api/leads/<form_id>')
    def api_leads(form_id):
        leads = ads_agent.meta_api.get_leads(form_id)
        return jsonify({'success': True, 'leads': leads})

    @app.route('/api/accounts')
    def api_accounts():
        return jsonify({'accounts': ads_agent.get_accounts(), 'current': ads_agent.meta_api.ad_account_id})

    @app.route('/api/accounts/save', methods=['POST'])
    def api_save_account():
        data = request.get_json()
        aid = ads_agent.save_account(data)
        if aid:
            return jsonify({'success': True, 'id': aid})
        return jsonify({'success': False, 'error': 'Could not save account'}), 400

    @app.route('/api/accounts/delete/<account_id>', methods=['POST'])
    def api_delete_account(account_id):
        if ads_agent.delete_account(account_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Account not found'}), 404

    @app.route('/api/reports')
    def api_reports():
        campaigns = ads_agent.get_all_campaigns()
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

    @app.route('/api/posts/create', methods=['POST'])
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
            'message': message,
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
    def api_publish_post(post_id):
        result = scheduler.publish_now(post_id)
        if 'error' in result:
            err = result['error']
            if isinstance(err, dict):
                err = err.get('message', str(err))
            return jsonify({'success': False, 'error': err}), 400
        return jsonify({'success': True, 'result': result})

    @app.route('/api/posts/delete/<post_id>', methods=['POST'])
    def api_delete_post(post_id):
        if scheduler.delete_post(post_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Post not found'}), 404

    @app.route('/api/posts/restore/<post_id>', methods=['POST'])
    def api_restore_post(post_id):
        if scheduler.restore_post(post_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Post not found'}), 404

    @app.route('/api/posts/delete-forever/<post_id>', methods=['POST'])
    def api_delete_forever(post_id):
        if scheduler.delete_forever(post_id):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Post not found'}), 404

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

    app = create_web_interface(agent)

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

    t = threading.Thread(target=auto_optimize_loop, daemon=True)
    t.start()

    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == '__main__':
    main()
