import os
from datetime import timedelta
import random
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from passlib.context import CryptContext
from dotenv import load_dotenv
import libsql
import json
from datetime import datetime
import uuid
from airtable import Airtable
import string
import requests


# Load environment variables
load_dotenv()

# --- Configuration ---
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET_KEY", "your-super-secret-session-key-shhh")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)

# --- Turso Database Configuration ---
DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

# --- Security Utilities ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Utility Function to Generate Affiliate Code ---
# RENAME THIS FUNCTION
def generate_random_affiliate_id(): 
    """Generates a unique affiliate code in the format CS-XXXXXX"""
    # Generate 6 random alphanumeric characters
    characters = string.ascii_uppercase + string.digits
    random_suffix = ''.join(random.choice(characters) for _ in range(6))
    return f"CS-{random_suffix}"

class FormDatabase:
    def __init__(self):
        # Fetch credentials from environment variables
        self.API_KEY = os.getenv('AIRTABLE_API_KEY')
        self.BASE_ID = os.getenv('AIRTABLE_BASE_ID')
        
        # Affiliate Table
        self.AFFILIATE_TABLE_NAME = os.getenv('AIRTABLE_AFFILIATE_TABLE_NAME', 'tbltvPCLo2wMo8kou')
        self.airtable_affiliate = Airtable(self.BASE_ID, self.AFFILIATE_TABLE_NAME, self.API_KEY)
        
        # Original Table (for the old /submit-signup route)
        self.SIGNUP_TABLE_NAME = os.getenv('AIRTABLE_TABLE_NAME', 'Dispaly')
        self.airtable_signup = Airtable(self.BASE_ID, self.SIGNUP_TABLE_NAME, self.API_KEY)

        # *** CRITICAL CORRECTION: Added Initialization for the Users Onboarding Table ***
        self.USERS_TABLE_NAME = os.getenv('AIRTABLE_USERS_TABLE_NAME', 'Users')
        self.airtable_users = Airtable(self.BASE_ID, self.USERS_TABLE_NAME, self.API_KEY)

    # --- Database Saving Methods ---
    
    def save_creator_submission(self, record):
        """Saves creator onboarding data to the Users table."""
        try:
            self.airtable_users.insert(record)
            return True
        except Exception as e:
            print(f"Airtable Creator Onboarding Error: {str(e)}")
            return False

    def save_agency_submission(self, record):
        """Saves agency onboarding data to the Users table."""
        try:
            self.airtable_users.insert(record)
            return True
        except Exception as e:
            print(f"Airtable Agency Onboarding Error: {str(e)}")
            return False

# Helper function to track T&C acceptance
def track_tnc_acceptance(cursor, user_id, role='manager'):
    """Records the T&C acceptance in the user_tnc_check table."""
    try:
        # Determine columns based on role
        influencer_id = user_id if role == 'creator' else None
        marketer_id = user_id if role == 'manager' else None
        
        # Insert record
        cursor.execute("""
            INSERT INTO user_tnc_check (influencer_id, marketer_id, accepted, count)
            VALUES (?, ?, 1, 1)
        """, [influencer_id, marketer_id])
        
        return True
    except Exception as e:
        print(f"Error tracking T&C acceptance: {e}")
        return False
    
    def save_affiliate_submission(self, form_data, affiliate_code):
        """
        Save affiliate application to the Affiliate Airtable table.
        """
        # Extract data
        full_name = str(form_data.get('name') or form_data.get('fullName', ''))
        email = str(form_data.get('email', ''))
        phone = str(form_data.get('phone', ''))
        package = str(form_data.get('package', ''))
        social_link = str(form_data.get('socialLink', ''))
        
        # --- FIX STARTS HERE ---
        # Get the audience data. If it's not a list, make it one.
        audience = form_data.get('audience', [])
        
        # If the frontend sent a single string instead of a list, wrap it
        if isinstance(audience, str):
            audience = [audience]
        # If it's None or empty, ensure it's an empty list
        if not audience:
            audience = []
        # --- FIX ENDS HERE ---

        # Map form data to Airtable columns
        fields = {
            'Full Name': full_name,
            'Email': email,
            'Phone Number': phone,
            'Service Package': package,
            'Social Link': social_link,
            'Referral Audience': audience,  # Send the List directly, NOT a string
            'Affiliate Code': str(affiliate_code),
            'Submission Date': datetime.now().isoformat(),
            'Status': 'Pending'
        }
        
        print(f"Attempting to insert fields: {fields}")

        try:
            # Typecast=True allows Airtable to try and map text to select options automatically
            response = self.airtable_affiliate.insert(fields, typecast=True)
            
            if response and 'id' in response:
                print(f"Successfully inserted affiliate record with ID: {response['id']}")
                return True
            else:
                print(f"Airtable insertion failed: No record ID returned")
                return False
            
        except Exception as e:
            print(f"Airtable Submission Exception: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return False
        
db = FormDatabase()
def get_db_connection():
    """Establishes and returns a libSQL database connection."""
    if not DATABASE_URL or not AUTH_TOKEN:
        raise ConnectionError("Turso DB credentials not found. Check .env file.")
    
    try:
        conn = libsql.connect(
            database=DATABASE_URL, 
            auth_token=AUTH_TOKEN
        )
        return conn, conn.cursor()
    except Exception as e:
        app.logger.error(f"libSQL Connection Error: {e}")
        raise ConnectionError("Failed to connect to Turso database using libsql.")

def hash_password(password: str) -> str:
    """Hashes a plaintext password."""
    return pwd_context.hash(password)

def check_password_hash(hashed_password: str, password: str) -> bool:
    """Checks a plaintext password against a hash."""
    return pwd_context.verify(password, hashed_password)

def login_required(role=None):
    """Decorator to require login and optionally check role."""
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({"message": "Authentication required"}), 401
            
            if role and session.get('role') != role:
                return jsonify({"message": f"Unauthorized - {role} access required"}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def update_creator_analytics(influencer_id, campaign_id, post_data):
    """Updates creator analytics when a post is submitted or performance data is updated."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # Insert or update post performance
        cursor.execute("""
            INSERT OR REPLACE INTO post_performance 
            (influencer_id, campaign_id, post_title, platform, post_url, 
             total_views, total_interactions, earnings, status, posted_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, date('now'))
        """, [
            influencer_id, campaign_id, 
            post_data.get('post_title', 'Untitled Post'),
            post_data.get('platform', 'Unknown'),
            post_data.get('post_url', ''),
            post_data.get('total_views', 0),
            post_data.get('total_interactions', 0),
            post_data.get('earnings', 0.00),
            post_data.get('status', 'Tracked')
        ])
        
        # Update earnings history
        earnings = post_data.get('earnings', 0.00)
        if earnings > 0:
            cursor.execute("""
                INSERT INTO earnings_history 
                (influencer_id, earnings_date, amount, source_type, source_id)
                VALUES (?, date('now'), ?, 'post', ?)
            """, [influencer_id, earnings, campaign_id])
        
        # Recalculate financial summary
        recalculate_financial_summary(cursor, influencer_id)
        
        conn.commit()
        
    except Exception as e:
        app.logger.error(f"Update Creator Analytics Error: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()

# --- UPDATE THIS FUNCTION IN app.py ---

def recalculate_financial_summary(cursor, influencer_id):
    """
    Recalculates a creator's financial standing from scratch to ensure accuracy.
    This prevents 'drift' where the dashboard numbers don't match actual post data.
    """
    try:
        # 1. Total Earnings (Lifetime): Sum of all posts marked as 'paid'
        # We DO NOT count 'approved' (pending) posts here, only finalized 'paid' ones.
        cursor.execute("""
            SELECT COALESCE(SUM(earnings), 0) 
            FROM submitted_posts 
            WHERE influencer_id = ? AND status = 'paid'
        """, [influencer_id])
        total_earnings = cursor.fetchone()[0] or 0.00
        
        # 2. Pending Earnings: Sum of posts approved but not yet 'paid' (Potential earnings)
        cursor.execute("""
            SELECT COALESCE(SUM(earnings), 0) 
            FROM submitted_posts 
            WHERE influencer_id = ? AND status = 'approved'
        """, [influencer_id])
        pending_earnings = cursor.fetchone()[0] or 0.00
        
        # 3. Total Payouts: Sum of all COMPLETED withdrawals
        # This is money that has successfully left the platform to the user's bank.
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) 
            FROM payout_history 
            WHERE influencer_id = ? AND status = 'Completed'
        """, [influencer_id])
        total_payouts = cursor.fetchone()[0] or 0.00
        
        # 4. Current Balance (Wallet): 
        # (Total Money Earned) - (Total Money Withdrawn)
        current_balance = total_earnings - total_payouts
        
        # 5. Update the Financials Table
        cursor.execute("""
            INSERT OR REPLACE INTO creator_financials 
            (influencer_id, total_earnings, current_balance, pending_earnings, last_updated)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [influencer_id, total_earnings, current_balance, pending_earnings])
        
        # 6. Update Legacy Influencers Table (for backward compatibility)
        cursor.execute("""
            UPDATE influencers 
            SET total_earnings = ?, current_balance = ?
            WHERE id = ?
        """, [total_earnings, current_balance, influencer_id])
        
    except Exception as e:
        app.logger.error(f"Recalculate Financial Summary Error: {e}")
        raise e
def get_campaign_analytics_data(campaign_id):
    """
    Fetches comprehensive analytics data for a specific campaign.
    """
    conn, cursor = None, None
    try:
        conn, cursor = get_db_connection()
        
        # 1. Fetch Campaign Summary
        cursor.execute("SELECT title, client_company, total_budget, budget_used, campaign_views, total_earnings, average_cpm FROM campaigns WHERE id = ?", [campaign_id])
        campaign = cursor.fetchone()
        
        if not campaign:
            return None
            
        campaign_data = dict(zip([col[0] for col in cursor.description], campaign))
        
        # 2. Fetch Creator Performance Data
        # This query joins influencers, application, and aggregates post_performance
        # We only consider 'accepted' influencers
        creator_performance_query = """
        SELECT 
            i.id AS influencer_id,
            i.name,
            i.instagram,
            SUM(pp.views) AS total_views,
            SUM(pp.likes + pp.comments + pp.shares) AS total_engagement,
            ROUND(AVG(pp.engagement_rate), 2) AS avg_engagement_rate
        FROM application a
        JOIN influencers i ON a.influencer_id = i.id
        LEFT JOIN post_performance pp ON a.influencer_id = pp.influencer_id AND a.campaign_id = pp.campaign_id
        WHERE a.campaign_id = ? AND a.status = 'accepted'
        GROUP BY i.id, i.name, i.instagram
        ORDER BY total_views DESC
        """
        cursor.execute(creator_performance_query, [campaign_id])
        creator_performance = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
        
        # 3. Fetch Platform Breakdown for Chart (Example)
        # Sum of views per platform
        platform_breakdown_query = """
        SELECT 
            platform, 
            SUM(views) AS total_views
        FROM post_performance 
        WHERE campaign_id = ?
        GROUP BY platform
        """
        cursor.execute(platform_breakdown_query, [campaign_id])
        platform_breakdown = dict(cursor.fetchall())
        
        # Combine all data
        analytics_data = {
            'campaign': campaign_data,
            'creators': creator_performance,
            'platform_breakdown': platform_breakdown
        }
        
        return analytics_data

    except Exception as e:
        app.logger.error(f"Database error fetching analytics for campaign {campaign_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()

@app.route('/api/affiliate-analytics/overview', methods=['GET'])
@login_required(role='manager')
def get_affiliate_analytics_overview():
    """Gets overview statistics, ensuring conversions are based on converted=1 status."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # Total affiliate campaigns
        cursor.execute("SELECT COUNT(*) FROM affiliate_campaigns WHERE manager_id = ?", [manager_id])
        total_campaigns = cursor.fetchone()[0] or 0
        
        # Live campaigns
        cursor.execute("SELECT COUNT(*) FROM affiliate_campaigns WHERE manager_id = ? AND status = 'live'", [manager_id])
        live_campaigns = cursor.fetchone()[0] or 0
        
        # Total conversions (FIXED: Filter by converted=1)
        cursor.execute("""
            SELECT COUNT(acl.id)
            FROM affiliate_clicks acl
            JOIN affiliate_code ac ON acl.affiliate_code_id = ac.id
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE afc.manager_id = ? AND acl.converted = 1
        """, [manager_id])
        total_conversions = cursor.fetchone()[0] or 0
        
        # Total clicks (New Calculation for accurate Rate)
        cursor.execute("""
            SELECT COUNT(acl.id)
            FROM affiliate_clicks acl
            JOIN affiliate_code ac ON acl.affiliate_code_id = ac.id
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE afc.manager_id = ?
        """, [manager_id])
        total_clicks = cursor.fetchone()[0] or 0
        
        # Total commission paid (FIXED: Based on actual conversions)
        cursor.execute("""
            SELECT COALESCE(SUM(afc.flat_fee), 0)
            FROM affiliate_clicks acl
            JOIN affiliate_code ac ON acl.affiliate_code_id = ac.id
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE afc.manager_id = ? AND acl.converted = 1
        """, [manager_id])
        total_commission = cursor.fetchone()[0] or 0
        
        # Active influencers
        cursor.execute("""
            SELECT COUNT(DISTINCT ac.influencer_id)
            FROM affiliate_code ac
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE afc.manager_id = ?
        """, [manager_id])
        active_influencers = cursor.fetchone()[0] or 0
        
        conversion_rate = (total_conversions / total_clicks * 100) if total_clicks > 0 else 0
        
        return jsonify({
            "total_campaigns": total_campaigns,
            "live_campaigns": live_campaigns,
            "total_conversions": total_conversions,
            "total_commission": float(total_commission),
            "active_influencers": active_influencers,
            "conversion_rate": round(conversion_rate, 2),
            "total_clicks": total_clicks
        }), 200
        
    except Exception as e:
        app.logger.error(f"Affiliate Analytics Overview Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

def update_campaign_shortlist(cursor, campaign_id, influencer_ids):
    """Updates the shortlist for a campaign."""
    # 1. Clear existing shortlist for this campaign
    cursor.execute("DELETE FROM campaign_shortlist WHERE campaign_id = ?", [campaign_id])
    
    # 2. Insert new ids
    if influencer_ids and isinstance(influencer_ids, list):
        current_time = datetime.now().isoformat()
        for inf_id in influencer_ids:
            try:
                cursor.execute("""
                    INSERT INTO campaign_shortlist (campaign_id, influencer_id, added_at)
                    VALUES (?, ?, ?)
                """, [campaign_id, int(inf_id), current_time])
            except Exception as e:
                print(f"Error adding influencer {inf_id} to shortlist: {e}")

@app.route('/api/affiliate-analytics/campaigns', methods=['GET'])
@login_required(role='manager')
def get_affiliate_campaigns_analytics():
    """Gets detailed analytics for all affiliate campaigns, calculating conversions from click data."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # --- MODIFIED QUERY ---
        # Replaced usage of aff.use_count with subquery on affiliate_clicks
        cursor.execute("""
            SELECT 
                ac.id,
                ac.product_name,
                ac.brand,
                ac.status,
                ac.created_at,
                ac.flat_fee,
                ac.max_influencers,
                ac.kpi,
                COUNT(DISTINCT aff.id) as current_influencers,
                -- Subquery to get verified conversions (converted = 1)
                COALESCE(SUM(click_stats.verified_conversions), 0) as total_conversions,
                -- Commission based on verified conversions
                COALESCE(SUM(click_stats.verified_conversions * ac.flat_fee), 0) as total_commission,
                COALESCE(SUM(click_stats.total_clicks), 0) as total_clicks
            FROM affiliate_campaigns ac
            LEFT JOIN affiliate_code aff ON ac.id = aff.campaign_id
            LEFT JOIN (
                SELECT 
                    affiliate_code_id, 
                    COUNT(*) as total_clicks,
                    SUM(CASE WHEN converted = 1 THEN 1 ELSE 0 END) as verified_conversions
                FROM affiliate_clicks 
                GROUP BY affiliate_code_id
            ) click_stats ON aff.id = click_stats.affiliate_code_id
            WHERE ac.manager_id = ?
            GROUP BY ac.id
            ORDER BY ac.created_at DESC
        """, [manager_id])
        
        campaigns = []
        for row in cursor.fetchall():
            conversions = row[9] or 0
            clicks = row[11] or 0
            conversion_rate = (conversions / clicks * 100) if clicks > 0 else 0
            
            # Calculate performance score
            performance_score = calculate_affiliate_performance_score(
                conversions, 
                clicks,
                row[8] or 0,  # current_influencers
                row[6] or 0   # max_influencers
            )
            
            campaigns.append({
                "id": row[0],
                "product_name": row[1],
                "brand": row[2],
                "status": row[3],
                "created_at": row[4],
                "flat_fee": float(row[5]),
                "max_influencers": row[6],
                "kpi": row[7],
                "current_influencers": row[8],
                "total_conversions": conversions,
                "total_commission": float(row[10]),
                "total_clicks": clicks,
                "conversion_rate": round(conversion_rate, 2),
                "performance_score": performance_score,
                "fill_rate": round((row[8] / row[6] * 100) if row[6] > 0 else 0, 1)
            })
        
        return jsonify({"campaigns": campaigns}), 200
        
    except Exception as e:
        app.logger.error(f"Affiliate Campaigns Analytics Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()
@app.route('/api/affiliate-analytics/campaign/<int:campaign_id>', methods=['GET'])
@login_required(role='manager')
def get_affiliate_campaign_detailed_analytics(campaign_id):
    """Gets detailed analytics for a specific campaign, using strict conversion counting."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # 1. Verify campaign ownership
        cursor.execute("""
            SELECT id FROM affiliate_campaigns 
            WHERE id = ? AND manager_id = ?
        """, [campaign_id, manager_id])
        
        if not cursor.fetchone():
            return jsonify({"message": "Campaign not found or unauthorized"}), 404
        
        # 2. Campaign basic info
        cursor.execute("""
            SELECT 
                ac.product_name, ac.brand, ac.product_description,
                ac.kpi, ac.flat_fee, ac.max_influencers, ac.status,
                ac.created_at, ac.product_url
            FROM affiliate_campaigns ac
            WHERE ac.id = ?
        """, [campaign_id])
        
        campaign_info = cursor.fetchone()
        
        # 3. Performance metrics (MODIFIED)
        # Calculates totals by joining clicks and filtering converted=1
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT aff.id) as total_codes,
                COUNT(DISTINCT aff.influencer_id) as unique_influencers,
                -- Aggregated metrics from clicks table
                COALESCE(SUM(click_stats.verified_conversions), 0) as total_conversions,
                COALESCE(SUM(click_stats.verified_conversions * ac.flat_fee), 0) as total_commission,
                COALESCE(SUM(click_stats.total_clicks), 0) as total_clicks
            FROM affiliate_code aff
            JOIN affiliate_campaigns ac ON aff.campaign_id = ac.id
            LEFT JOIN (
                SELECT 
                    affiliate_code_id, 
                    COUNT(*) as total_clicks,
                    SUM(CASE WHEN converted = 1 THEN 1 ELSE 0 END) as verified_conversions
                FROM affiliate_clicks 
                GROUP BY affiliate_code_id
            ) click_stats ON aff.id = click_stats.affiliate_code_id
            WHERE ac.id = ?
        """, [campaign_id])
        
        perf_result = cursor.fetchone()
        
        # 4. Top performing influencers (MODIFIED)
        cursor.execute("""
            SELECT 
                i.name,
                i.instagram,
                aff.affiliate_code,
                COALESCE(click_stats.verified_conversions, 0) as conversions,
                COALESCE(click_stats.total_clicks, 0) as total_clicks,
                (COALESCE(click_stats.verified_conversions, 0) * ac.flat_fee) as total_commission
            FROM affiliate_code aff
            JOIN influencers i ON aff.influencer_id = i.id
            JOIN affiliate_campaigns ac ON aff.campaign_id = ac.id
            LEFT JOIN (
                SELECT 
                    affiliate_code_id, 
                    COUNT(*) as total_clicks,
                    SUM(CASE WHEN converted = 1 THEN 1 ELSE 0 END) as verified_conversions
                FROM affiliate_clicks 
                GROUP BY affiliate_code_id
            ) click_stats ON aff.id = click_stats.affiliate_code_id
            WHERE ac.id = ?
            ORDER BY conversions DESC, total_clicks DESC
            LIMIT 10
        """, [campaign_id])
        
        top_influencers = []
        for row in cursor.fetchall():
            clicks = row[4] or 0
            conversions = row[3] or 0
            conversion_rate = (conversions / clicks * 100) if clicks > 0 else 0
            
            top_influencers.append({
                "name": row[0],
                "instagram": row[1],
                "affiliate_code": row[2],
                "conversions": conversions,
                "clicks": clicks,
                "conversion_rate": round(conversion_rate, 2),
                "commission": float(row[5])
            })
        
        # 5. Daily Trends (Last 30 Days)
        # Fetch raw conversion data grouped by date (converted=1 only)
        cursor.execute("""
            SELECT 
                DATE(acl.clicked_at) as date,
                SUM(CASE WHEN acl.converted = 1 THEN 1 ELSE 0 END) as conversions
            FROM affiliate_clicks acl
            JOIN affiliate_code aff ON acl.affiliate_code_id = aff.id
            WHERE aff.campaign_id = ? 
                AND acl.clicked_at >= date('now', '-30 days')
            GROUP BY DATE(acl.clicked_at)
        """, [campaign_id])
        conversion_data = {row[0]: row[1] for row in cursor.fetchall()}

        # Fetch raw click data grouped by date
        cursor.execute("""
            SELECT 
                DATE(acl.clicked_at) as date,
                COUNT(*) as clicks
            FROM affiliate_clicks acl
            JOIN affiliate_code aff ON acl.affiliate_code_id = aff.id
            WHERE aff.campaign_id = ? 
                AND acl.clicked_at >= date('now', '-30 days')
            GROUP BY DATE(acl.clicked_at)
        """, [campaign_id])
        click_data = {row[0]: row[1] for row in cursor.fetchall()}

        # Generate complete 30-day timeline
        daily_trend = []
        today = datetime.now().date()
        
        for i in range(29, -1, -1):
            date_val = today - timedelta(days=i)
            date_str = date_val.strftime('%Y-%m-%d')
            
            daily_trend.append({
                "date": date_str,
                "conversions": conversion_data.get(date_str, 0),
                "clicks": click_data.get(date_str, 0)
            })
        
        # 6. Calculate final aggregated metrics
        total_conversions = perf_result[2] or 0
        total_commission = perf_result[3] or 0
        total_clicks = perf_result[4] or 0
        conversion_rate = (total_conversions / total_clicks * 100) if total_clicks > 0 else 0
        
        # Count codes with clicks
        cursor.execute("""
            SELECT COUNT(DISTINCT affiliate_code_id)
            FROM affiliate_clicks acl
            JOIN affiliate_code aff ON acl.affiliate_code_id = aff.id
            WHERE aff.campaign_id = ?
        """, [campaign_id])
        codes_with_clicks = cursor.fetchone()[0] or 0
        
        return jsonify({
            "campaign_info": {
                "product_name": campaign_info[0],
                "brand": campaign_info[1],
                "product_description": campaign_info[2],
                "kpi": campaign_info[3],
                "flat_fee": float(campaign_info[4]),
                "max_influencers": campaign_info[5],
                "status": campaign_info[6],
                "created_at": campaign_info[7],
                "product_url": campaign_info[8]
            },
            "performance_metrics": {
                "total_codes": perf_result[0],
                "total_conversions": total_conversions,
                "total_commission": float(total_commission),
                "unique_influencers": perf_result[1],
                "total_clicks": total_clicks,
                "conversion_rate": round(conversion_rate, 2),
                "codes_with_clicks": codes_with_clicks,
                "fill_rate": round((perf_result[1] / campaign_info[5] * 100) if campaign_info[5] > 0 else 0, 1)
            },
            "top_influencers": top_influencers,
            "daily_trend": daily_trend
        }), 200
        
    except Exception as e:
        app.logger.error(f"Affiliate Campaign Detailed Analytics Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()
@app.route('/api/affiliate-codes/<int:code_id>/clicks', methods=['GET'])
@login_required(role='manager')
def get_affiliate_code_clicks(code_id):
    """Gets the recent click history for a specific affiliate code."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # Verify ownership via campaign -> manager
        cursor.execute("""
            SELECT ac.id 
            FROM affiliate_code ac
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE ac.id = ? AND afc.manager_id = ?
        """, [code_id, manager_id])
        
        if not cursor.fetchone():
            return jsonify({"message": "Affiliate code not found or unauthorized"}), 404
            
        # Fetch clicks
        cursor.execute("""
            SELECT 
                id,
                clicked_at,
                ip_address,
                referrer,
                converted,
                converted_at
            FROM affiliate_clicks
            WHERE affiliate_code_id = ?
            ORDER BY clicked_at DESC
            LIMIT 50
        """, [code_id])
        
        clicks = []
        for row in cursor.fetchall():
            clicks.append({
                "id": row[0],
                "clicked_at": row[1],
                "ip_address": row[2],
                "referrer": row[3],
                "converted": bool(row[4]),
                "converted_at": row[5]
            })
            
        return jsonify({"clicks": clicks}), 200
        
    except Exception as e:
        app.logger.error(f"Get Affiliate Clicks Error: {e}")
        return jsonify({"message": "Internal server error"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/affiliate-clicks/<int:click_id>/convert', methods=['PUT'])
@login_required(role='manager')
def mark_click_converted(click_id):
    """Manually marks a click as converted."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # 1. Verify ownership and check if already converted
        cursor.execute("""
            SELECT acl.id, acl.converted, ac.id, ac.campaign_id
            FROM affiliate_clicks acl
            JOIN affiliate_code ac ON acl.affiliate_code_id = ac.id
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE acl.id = ? AND afc.manager_id = ?
        """, [click_id, manager_id])
        
        click_data = cursor.fetchone()
        
        if not click_data:
            return jsonify({"message": "Click record not found or unauthorized"}), 404
            
        if click_data[1]: # Already converted
            return jsonify({"message": "This click is already marked as converted"}), 400
            
        affiliate_code_id = click_data[2]
            
        # 2. Mark click as converted
        current_time = datetime.now().isoformat()
        cursor.execute("""
            UPDATE affiliate_clicks 
            SET converted = 1, converted_at = ?
            WHERE id = ?
        """, [current_time, click_id])
        
        # 3. Increment the use_count (conversion count) on the affiliate_code
        cursor.execute("""
            UPDATE affiliate_code 
            SET use_count = use_count + 1 
            WHERE id = ?
        """, [affiliate_code_id])
        
        conn.commit()
        
        return jsonify({"message": "Click successfully marked as converted"}), 200
        
    except Exception as e:
        app.logger.error(f"Mark Click Converted Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "Internal server error"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/affiliate-campaigns/<int:campaign_id>/status', methods=['PUT'])
@login_required(role='manager')
def update_affiliate_campaign_status(campaign_id):
    """
    Updates the status of an affiliate campaign (e.g., to 'completed').
    This is used for the 'End Campaign' button.
    """
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        new_status = data.get('status', 'completed')
        
        if new_status not in ['live', 'draft', 'completed']:
            return jsonify({"message": "Invalid campaign status"}), 400

        conn, cursor = get_db_connection()
        
        # Ensure the manager owns the campaign
        cursor.execute("""
            SELECT id FROM affiliate_campaigns 
            WHERE id = ? AND manager_id = ?
        """, [campaign_id, manager_id])
        
        if cursor.fetchone() is None:
            return jsonify({"message": "Campaign not found or unauthorized"}), 404
            
        # Update the campaign status
        cursor.execute("""
            UPDATE affiliate_campaigns
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [new_status, campaign_id])
        
        conn.commit()
        
        return jsonify({
            "message": f"Affiliate campaign {campaign_id} status updated to {new_status}"
        }), 200
        
    except Exception as e:
        app.logger.error(f"Update Affiliate Campaign Status Error: {e}")
        if conn: 
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/affiliate-campaigns/<int:campaign_id>/deactivate-codes', methods=['POST'])
@login_required(role='manager')
def deactivate_affiliate_codes(campaign_id):
    """
    Deactivates all affiliate codes for a campaign by deleting them.
    This is called when a campaign is ended.
    """
    conn = None
    try:
        manager_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # Verify campaign ownership
        cursor.execute("""
            SELECT id, status FROM affiliate_campaigns 
            WHERE id = ? AND manager_id = ?
        """, [campaign_id, manager_id])
        
        campaign = cursor.fetchone()
        if not campaign:
            return jsonify({"message": "Campaign not found or unauthorized"}), 404
        
        # Get count of codes to be deleted
        cursor.execute("""
            SELECT COUNT(*) FROM affiliate_code WHERE campaign_id = ?
        """, [campaign_id])
        
        code_count = cursor.fetchone()[0] or 0
        
        # Delete all affiliate codes for this campaign
        cursor.execute("""
            DELETE FROM affiliate_code WHERE campaign_id = ?
        """, [campaign_id])
        
        # Also delete associated clicks if the table exists
        try:
            cursor.execute("""
                DELETE FROM affiliate_clicks 
                WHERE affiliate_code_id IN (
                    SELECT id FROM affiliate_code WHERE campaign_id = ?
                )
            """, [campaign_id])
        except:
            pass  # Table might not exist or already deleted
        
        conn.commit()
        
        return jsonify({
            "message": f"Successfully deactivated {code_count} affiliate codes",
            "codes_deleted": code_count
        }), 200
        
    except Exception as e:
        app.logger.error(f"Deactivate Affiliate Codes Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

# Update the existing get_manager_affiliate_campaigns endpoint to include completed campaigns
@app.route('/api/affiliate-campaigns/manager', methods=['GET'])
@login_required(role='manager')
def get_manager_affiliate_campaigns():
    """Gets all affiliate campaigns for the logged-in manager, including completed ones."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                ac.id,
                ac.product_name,
                ac.brand,
                ac.kpi,
                ac.flat_fee,
                ac.max_influencers,
                ac.created_at,
                ac.product_description,
                ac.product_url,
                ac.min_profile_score,
                ac.status,
                ac.updated_at,
                COUNT(af.id) as current_influencers,
                COALESCE(SUM(af.use_count), 0) as total_conversions
            FROM affiliate_campaigns ac
            LEFT JOIN affiliate_code af ON ac.id = af.campaign_id
            WHERE ac.manager_id = ?
            GROUP BY ac.id
            ORDER BY 
                CASE 
                    WHEN ac.status = 'live' THEN 1
                    WHEN ac.status = 'draft' THEN 2
                    WHEN ac.status = 'completed' THEN 3
                    ELSE 4
                END,
                ac.created_at DESC
        """, [manager_id])
        
        campaigns = cursor.fetchall()
        
        campaign_list = []
        for campaign in campaigns:
            campaign_list.append({
                'id': campaign[0],
                'product_name': campaign[1],
                'brand': campaign[2],
                'kpi': campaign[3],
                'flat_fee': float(campaign[4] or 0),
                'max_influencers': campaign[5],
                'created_at': campaign[6],
                'product_description': campaign[7],
                'product_url': campaign[8],
                'min_profile_score': campaign[9] or 0,
                'status': campaign[10],
                'updated_at': campaign[11],
                'current_influencers': campaign[12],
                'total_conversions': campaign[13]
            })
        
        return jsonify({'campaigns': campaign_list})
        
    except Exception as e:
        app.logger.error(f"Error fetching manager affiliate campaigns: {e}")
        return jsonify({'campaigns': []})
    finally:
        if conn:
            conn.close()

@app.route('/api/affiliate-analytics/conversion-trends', methods=['GET'])
@login_required(role='manager')
def get_affiliate_conversion_trends():
    """Gets conversion trends for affiliate campaigns over time with breakdown."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # 1. Main Daily Trend (Totals) - Already correct
        cursor.execute("""
            SELECT 
                DATE(acl.clicked_at) as date,
                COUNT(*) as clicks,
                SUM(CASE WHEN acl.converted = 1 THEN 1 ELSE 0 END) as conversions
            FROM affiliate_clicks acl
            JOIN affiliate_code aff ON acl.affiliate_code_id = aff.id
            JOIN affiliate_campaigns ac ON aff.campaign_id = ac.id
            WHERE ac.manager_id = ? 
                AND acl.clicked_at >= date('now', '-30 days')
            GROUP BY DATE(acl.clicked_at)
            ORDER BY date DESC
        """, [manager_id])
        
        daily_data_map = {}
        for row in cursor.fetchall():
            clicks = row[1] or 0
            conversions = row[2] or 0
            conversion_rate = (conversions / clicks * 100) if clicks > 0 else 0
            
            daily_data_map[row[0]] = {
                "date": row[0],
                "clicks": clicks,
                "conversions": conversions,
                "conversion_rate": round(conversion_rate, 2),
                "breakdown": []
            }
        
        # 2. Breakdown by Campaign per Day - Already correct
        cursor.execute("""
            SELECT 
                DATE(acl.clicked_at) as date,
                ac.product_name,
                COUNT(*) as clicks,
                SUM(CASE WHEN acl.converted = 1 THEN 1 ELSE 0 END) as conversions
            FROM affiliate_clicks acl
            JOIN affiliate_code aff ON acl.affiliate_code_id = aff.id
            JOIN affiliate_campaigns ac ON aff.campaign_id = ac.id
            WHERE ac.manager_id = ? 
                AND acl.clicked_at >= date('now', '-30 days')
            GROUP BY DATE(acl.clicked_at), ac.product_name
            ORDER BY date DESC, clicks DESC
        """, [manager_id])

        for row in cursor.fetchall():
            date_key = row[0]
            if date_key in daily_data_map:
                daily_data_map[date_key]["breakdown"].append({
                    "campaign_name": row[1],
                    "clicks": row[2] or 0,
                    "conversions": row[3] or 0
                })

        daily_trend = list(daily_data_map.values())
        daily_trend.sort(key=lambda x: x['date'])

        # 3. Campaign performance comparison (FIXED)
        # Previously used 'aff.use_count' which counted clicks as conversions.
        # Now joins with a subquery that separates clicks vs real conversions.
        cursor.execute("""
            SELECT 
                ac.product_name,
                ac.brand,
                COUNT(DISTINCT aff.id) as total_codes,
                COALESCE(SUM(click_stats.real_conversions), 0) as conversions,
                COALESCE(SUM(click_stats.clicks), 0) as clicks,
                COALESCE(SUM(click_stats.real_conversions * ac.flat_fee), 0) as commission
            FROM affiliate_campaigns ac
            LEFT JOIN affiliate_code aff ON ac.id = aff.campaign_id
            LEFT JOIN (
                SELECT 
                    affiliate_code_id, 
                    COUNT(*) as clicks, -- Counts ALL clicks
                    SUM(CASE WHEN converted = 1 THEN 1 ELSE 0 END) as real_conversions -- Counts ONLY conversions
                FROM affiliate_clicks 
                GROUP BY affiliate_code_id
            ) click_stats ON aff.id = click_stats.affiliate_code_id
            WHERE ac.manager_id = ?
            GROUP BY ac.id
            ORDER BY conversions DESC
        """, [manager_id])
        
        campaign_comparison = []
        for row in cursor.fetchall():
            clicks = row[4] or 0
            conversions = row[3] or 0
            conversion_rate = (conversions / clicks * 100) if clicks > 0 else 0
            
            campaign_comparison.append({
                "product_name": row[0],
                "brand": row[1],
                "total_codes": row[2],
                "conversions": conversions,
                "clicks": clicks,
                "conversion_rate": round(conversion_rate, 2),
                "commission": float(row[5])
            })
        
        return jsonify({
            "daily_trend": daily_trend,
            "campaign_comparison": campaign_comparison
        }), 200
        
    except Exception as e:
        app.logger.error(f"Affiliate Conversion Trends Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()
def calculate_affiliate_performance_score(conversions, clicks, current_influencers, max_influencers):
    """Calculate a performance score for affiliate campaigns (0-100)."""
    score = 50  # Base score
    
    # Conversion rate (max 30 points)
    conversion_rate = (conversions / clicks * 100) if clicks > 0 else 0
    if conversion_rate >= 10:
        score += 30
    elif conversion_rate >= 5:
        score += 20
    elif conversion_rate >= 2:
        score += 10
    elif conversion_rate > 0:
        score += 5
    
    # Influencer activation (max 20 points)
    fill_rate = (current_influencers / max_influencers * 100) if max_influencers > 0 else 0
    if fill_rate >= 80:
        score += 20
    elif fill_rate >= 50:
        score += 15
    elif fill_rate >= 25:
        score += 10
    elif fill_rate > 0:
        score += 5
    
    # Absolute performance (max 30 points)
    if conversions >= 100:
        score += 30
    elif conversions >= 50:
        score += 25
    elif conversions >= 20:
        score += 20
    elif conversions >= 10:
        score += 15
    elif conversions >= 5:
        score += 10
    elif conversions > 0:
        score += 5
    
    return min(100, max(0, score))
# --- API Routes (Authentication) ---

@app.route('/api/debug/campaign/<int:campaign_id>')
@login_required(role='manager')
def debug_campaign_data(campaign_id):
    """Debug endpoint to check campaign data structure."""
    analytics_data = get_campaign_analytics_data(campaign_id)
    return jsonify(analytics_data)

@app.route('/api/agency-stats', methods=['GET'])
@login_required(role='manager')
def get_agency_stats():
    """Gets agency statistics for the logged-in manager."""
    conn = None
    try:
        manager_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # Get total budget allocated
        cursor.execute("SELECT COALESCE(SUM(total_budget), 0) FROM campaigns WHERE manager_id = ? AND status != 'draft'", [manager_id])
        total_budget_allocated = cursor.fetchone()[0] or 0
        
        # Get budget spent (placeholder)
        budget_spent = total_budget_allocated * 0.35
        
        # Get compliance score (placeholder)
        compliance_score = 92
        
        return jsonify({
            "total_budget_allocated": float(total_budget_allocated),
            "budget_spent": float(budget_spent),
            "compliance_score": compliance_score
        }), 200
        
    except Exception as e:
        app.logger.error(f"Agency Stats Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Handles user login and creates session."""
    conn = None
    try:
        conn, cursor = get_db_connection()
    except ConnectionError as e:
        return jsonify({"message": "Database configuration error."}), 500

    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    role = data.get('role')

    if not email or not password or not role:
        return jsonify({"message": "Missing email, password, or role"}), 400

    table_name = ""
    email_column = ""

    if role == 'creator':
        table_name = "influencers"
        email_column = "email"
    elif role == 'manager':
        table_name = "marketers"
        email_column = "log_email" 
    else:
        if conn: conn.close()
        return jsonify({"message": "Invalid role specified"}), 400
    
    try:
        query = f"SELECT id, password FROM {table_name} WHERE {email_column} = ?"
        cursor.execute(query, [email])
        user_row = cursor.fetchone()

        if user_row:
            user_id = user_row[0]
            hashed_pass = user_row[1]

            if check_password_hash(hashed_pass, password):
                # Create session
                session.permanent = True
                session['user_id'] = user_id
                session['role'] = role
                session['email'] = email
                
                conn.close()
                return jsonify({"message": "Login successful", "role": role}), 200
            
        conn.close()
        return jsonify({"message": "Invalid credentials"}), 401
    
    except Exception as e:
        app.logger.error(f"Login Query Error: {e}")
        if conn: conn.close()
        return jsonify({"message": "An internal server error occurred during login."}), 500

@app.route('/api/campaigns/all', methods=['GET'])
@login_required(role='manager')
def get_all_campaigns():
    """Gets all campaigns for the logged-in manager with optimized analytics."""
    conn = None
    try:
        manager_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # --- OPTIMIZED QUERY ---
        # We fetch campaign details AND aggregate stats in a single query using subqueries.
        # This reduces database calls from N+1 to 1.
        query = """
            SELECT 
                c.id, c.title, c.client_company, c.status, c.total_budget, 
                c.start_date, c.end_date, c.created_at, c.platforms, c.cpm_rate,
                
                -- Subquery 1: Aggregate Post Stats (Views, Cost, Engagement, Active Creators)
                (SELECT COALESCE(SUM(views), 0) FROM submitted_posts 
                 WHERE campaign_id = c.id AND status IN ('approved', 'paid')) as total_views,
                 
                (SELECT COALESCE(SUM(earnings), 0) FROM submitted_posts 
                 WHERE campaign_id = c.id AND status IN ('approved', 'paid')) as total_cost,
                 
                (SELECT COALESCE(SUM(engagement), 0) FROM submitted_posts 
                 WHERE campaign_id = c.id AND status IN ('approved', 'paid')) as total_engagement,
                 
                (SELECT COUNT(DISTINCT influencer_id) FROM submitted_posts 
                 WHERE campaign_id = c.id AND status IN ('approved', 'paid')) as active_creators,

                -- Subquery 2: Count Total Approved/Accepted Creators
                (SELECT COUNT(id) FROM applications 
                 WHERE campaign_id = c.id AND status IN ('accepted', 'approved', 'drafting_content')) as total_creators

            FROM campaigns c 
            WHERE c.manager_id = ?
            ORDER BY 
                CASE 
                    WHEN c.status = 'live' THEN 1
                    WHEN c.status = 'paused' THEN 2
                    WHEN c.status = 'draft' THEN 3
                    WHEN c.status = 'completed' THEN 4
                    ELSE 5
                END,
                c.created_at DESC
        """
        
        cursor.execute(query, [manager_id])
        
        campaigns = []
        for row in cursor.fetchall():
            # Unpack the row
            c_id, title, client, status, budget_str, start, end, created, platforms_json, cpm_str = row[0:10]
            total_views, total_cost, total_engagement, active_creators, total_creators = row[10:15]

            # Parse basic fields
            platforms = []
            try:
                platforms = json.loads(platforms_json) if platforms_json else []
            except:
                platforms = []
            
            # Parse Numbers
            total_budget = float(budget_str) if budget_str else 0.0
            cpm_rate = float(cpm_str) if cpm_str else 0.0
            total_cost = float(total_cost)
            total_views = int(total_views)
            
            # Calculate Days Remaining
            days_remaining = 0
            if end:
                try:
                    end_date = datetime.fromisoformat(end)
                    days_remaining = max(0, (end_date - datetime.now()).days)
                except:
                    days_remaining = 0

            # --- CALCULATE ANALYTICS IN PYTHON (No DB Calls) ---
            budget_consumed_percentage = (total_cost / total_budget * 100) if total_budget > 0 else 0.00
            effective_cpm = (total_cost / total_views * 1000) if total_views > 0 else 0.00
            
            # Health Score Calculation
            score = 75 # Base
            ecpm_target = cpm_rate if cpm_rate > 0 else 30.0
            
            # 1. Budget Efficiency
            if 20 <= budget_consumed_percentage <= 80: score += 10
            elif budget_consumed_percentage > 90: score -= 15
            elif budget_consumed_percentage < 10: score -= 5
            
            # 2. Cost Efficiency
            if total_views > 0:
                if effective_cpm <= ecpm_target: score += 20
                elif effective_cpm <= (ecpm_target * 1.25): score += 5
                elif effective_cpm > (ecpm_target * 1.5): score -= 10
            
            # 3. Creator Activity
            if total_creators > 0 and (active_creators / total_creators) >= 0.75:
                score += 5

            health_score = max(0, min(100, score))

            # Construct Analytics Object
            analytics = {
                "budget_consumed": round(total_cost, 2),
                "budget_consumed_percentage": round(budget_consumed_percentage, 0),
                "creators_active": active_creators,
                "creators_total": total_creators,
                "effective_cpm": round(effective_cpm, 2),
                "total_views": total_views,
                "total_engagement": total_engagement,
                "conversions": int(total_views * 0.01),
                "health_score": round(health_score, 0),
                "days_remaining": days_remaining
            }

            campaigns.append({
                "id": c_id,
                "title": title,
                "client_company": client,
                "status": status,
                "total_budget": total_budget,
                "start_date": start,
                "end_date": end,
                "created_at": created,
                "platforms": platforms,
                "analytics": analytics
            })
        
        return jsonify({"campaigns": campaigns}), 200
        
    except Exception as e:
        app.logger.error(f"Get All Campaigns Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()
@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Handles user logout and clears session."""
    session.clear()
    return jsonify({"message": "Logout successful"}), 200

@app.route('/api/auth/register', methods=['POST'])
def register():
    """Handles user registration for both creator and manager roles."""
    conn = None
    try:
        data = request.get_json()
        role = data.get('role')
        password = data.get('password')
        
        if not role or not password:
            return jsonify({"message": "Missing role or password"}), 400

        hashed_password = hash_password(password)
        conn, cursor = get_db_connection()
        
        message = "Registration successful"

        if role == 'creator':
            name = data.get('name')
            email = data.get('email')
            
            if not email or not name:
                return jsonify({"message": "Missing required creator fields (email, name)"}), 400
                
            instagram = data.get('instagram', '')
            tik_tok = data.get('tik_tok', '')
            youtube = data.get('youtube', '')
            avg_views = int(data.get('avg_views', 0))
            reach = int(data.get('reach', 0))
            number = data.get('number', '')

            query = """
                INSERT INTO influencers 
                (name, instagram, tik_tok, youtube, avg_views, reach, email, number, password) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            values = [name, instagram, tik_tok, youtube, avg_views, reach, email, number, hashed_password]
            cursor.execute(query, values)
            
        elif role == 'manager':
            company = data.get('company')
            log_email = data.get('log_email')

            if not log_email or not company:
                return jsonify({"message": "Missing required manager fields (log_email, company)"}), 400

            ad_number = data.get('ad_number', '')
            ad_email = data.get('ad_email', '')
            location = data.get('location', '')
            category = data.get('category', '')
            brand_description = data.get('brand_description', '')
            camp_spend = data.get('camp_spend', '')
            camp_views = data.get('camp_views', '')
            
            query = """
                INSERT INTO marketers 
                (company, ad_number, ad_email, location, category, password, log_email, brand_description, camp_spend, camp_views) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            values = [company, ad_number, ad_email, location, category, hashed_password, log_email, brand_description, camp_spend, camp_views]
            cursor.execute(query, values)
            
        else:
            if conn: conn.close()
            return jsonify({"message": "Invalid role specified"}), 400

        conn.commit()
        conn.close()
        return jsonify({"message": message}), 201

    except Exception as e:
        error_str = str(e).lower()
        if 'unique constraint failed' in error_str or 'duplicate' in error_str:
             message = "Account with this email already exists."
             status_code = 409
        else:
            app.logger.error(f"Registration Error: {e}")
            message = "An internal server error occurred during registration."
            status_code = 500

        if conn: conn.close()
        return jsonify({"message": message}), status_code

@app.route('/api/affiliate-campaigns/live')
def get_live_affiliate_campaigns():
    conn = None
    cursor = None
    try:
        conn, cursor = get_db_connection()
        
        # First, check if affiliate_campaigns table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='affiliate_campaigns'
        """)
        table_exists = cursor.fetchone()
        
        if not table_exists:
            return jsonify({'campaigns': []})
        
        # Get live campaigns with influencer count and total conversions from affiliate_code table
        cursor.execute("""
            SELECT 
                ac.id,
                ac.product_name,
                ac.brand,
                ac.kpi,
                ac.flat_fee,
                ac.max_influencers,
                ac.created_at,
                ac.product_description,
                ac.product_url,
                ac.min_profile_score,
                ac.status,
                COUNT(af.id) as current_influencers,
                COALESCE(SUM(af.use_count), 0) as total_conversions
            FROM affiliate_campaigns ac
            LEFT JOIN affiliate_code af ON ac.id = af.campaign_id
            WHERE ac.status = 'live'
            GROUP BY ac.id
            ORDER BY ac.created_at DESC
        """)
        campaigns = cursor.fetchall()
        
        campaign_list = []
        for campaign in campaigns:
            campaign_list.append({
                'id': campaign[0],
                'product_name': campaign[1],
                'brand': campaign[2],
                'kpi': campaign[3],
                'commission': float(campaign[4] or 0),
                'flat_fee': float(campaign[4] or 0),  # Add flat_fee as alias for commission
                'max_influencers': campaign[5],
                'created_at': campaign[6],
                'product_description': campaign[7],
                'product_url': campaign[8],
                'min_profile_score': campaign[9] or 0,
                'status': campaign[10],
                'current_influencers': campaign[11],  # This is the count from affiliate_code table
                'total_conversions': campaign[12]  # Sum of all use_counts
            })
            
        return jsonify({'campaigns': campaign_list})
        
    except Exception as e:
        print(f"Error fetching affiliate campaigns: {e}")
        return jsonify({'campaigns': []})
    finally:
        if conn:
            conn.close()

# --- Profile/Settings API Routes ---

@app.route('/api/creator/profile', methods=['GET'])
@login_required(role='creator')
def get_creator_profile():
    """Gets the creator's profile information, including new profile details."""
    conn = None
    try:
        creator_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # Updated query to fetch new fields
        cursor.execute("""
            SELECT 
                id, name, email, number, instagram, tik_tok, youtube,
                avg_views, reach, profile_score, total_earnings, current_balance,
                location, category, bio, best_content_link
            FROM influencers 
            WHERE id = ?
        """, [creator_id])
        
        profile = cursor.fetchone()
        
        if not profile:
            return jsonify({"message": "Profile not found"}), 404
        
        return jsonify({
            "id": profile[0],
            "name": profile[1],
            "email": profile[2],
            "number": profile[3],
            "instagram": profile[4],
            "tik_tok": profile[5],
            "youtube": profile[6],
            "avg_views": profile[7] or 0,
            "reach": profile[8] or 0,
            "profile_score": profile[9] or 0,
            "total_earnings": float(profile[10]) if profile[10] else 0.00,
            "current_balance": float(profile[11]) if profile[11] else 0.00,
            # New fields mappings
            "location": profile[12] or "",
            "category": profile[13] or "",
            "bio": profile[14] or "",
            "best_content_link": profile[15] or ""
        }), 200
        
    except Exception as e:
        app.logger.error(f"Get Creator Profile Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()
@app.route('/api/creator/profile', methods=['PUT'])
@login_required(role='creator')
def update_creator_profile():
    """Updates the creator's profile information."""
    conn = None
    try:
        creator_id = session.get('user_id')
        data = request.get_json()
        
        conn, cursor = get_db_connection()
        
        # Build update query dynamically based on provided fields
        # FIXED: Added the new profile fields to allowed_fields
        allowed_fields = ['name', 'number', 'instagram', 'tik_tok', 'youtube', 'avg_views', 'reach', 
                         'location', 'category', 'bio', 'best_content_link']
        update_fields = []
        values = []
        
        for field in allowed_fields:
            if field in data:
                update_fields.append(f"{field} = ?")
                values.append(data[field])
        
        if not update_fields:
            return jsonify({"message": "No fields to update"}), 400
        
        values.append(creator_id)
        query = f"UPDATE influencers SET {', '.join(update_fields)} WHERE id = ?"
        
        app.logger.info(f"Updating creator {creator_id} with query: {query}")
        app.logger.info(f"Values: {values}")
        
        cursor.execute(query, values)
        conn.commit()
        
        return jsonify({"message": "Profile updated successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Update Creator Profile Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/change-password', methods=['PUT'])
@login_required(role='creator')
def change_creator_password():
    """Changes the creator's password."""
    conn = None
    try:
        creator_id = session.get('user_id')
        data = request.get_json()
        
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        
        if not current_password or not new_password:
            return jsonify({"message": "Current and new password are required"}), 400
        
        if len(new_password) < 8:
            return jsonify({"message": "New password must be at least 8 characters"}), 400
        
        conn, cursor = get_db_connection()
        
        # Verify current password
        cursor.execute("SELECT password FROM influencers WHERE id = ?", [creator_id])
        result = cursor.fetchone()
        
        if not result or not check_password_hash(result[0], current_password):
            return jsonify({"message": "Current password is incorrect"}), 401
        
        # Update to new password
        hashed_new_password = hash_password(new_password)
        cursor.execute("UPDATE influencers SET password = ? WHERE id = ?", 
                      [hashed_new_password, creator_id])
        conn.commit()
        
        return jsonify({"message": "Password changed successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Change Password Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/delete-account', methods=['DELETE'])
@login_required(role='creator')
def delete_creator_account():
    """Deletes the creator's account and all associated data."""
    conn = None
    try:
        creator_id = session.get('user_id')
        data = request.get_json()
        
        password = data.get('password')
        confirmation = data.get('confirmation')
        
        if not password:
            return jsonify({"message": "Password is required"}), 400
        
        if confirmation != "DELETE":
            return jsonify({"message": "Please type DELETE to confirm"}), 400
        
        conn, cursor = get_db_connection()
        
        # Verify password
        cursor.execute("SELECT password FROM influencers WHERE id = ?", [creator_id])
        result = cursor.fetchone()
        
        if not result or not check_password_hash(result[0], password):
            return jsonify({"message": "Incorrect password"}), 401
        
        # Delete associated data (cascade delete)
        cursor.execute("DELETE FROM applications WHERE influencer_id = ?", [creator_id])
        cursor.execute("DELETE FROM submitted_posts WHERE influencer_id = ?", [creator_id])
        cursor.execute("DELETE FROM post_performance WHERE influencer_id = ?", [creator_id])
        cursor.execute("DELETE FROM creator_financials WHERE influencer_id = ?", [creator_id])
        cursor.execute("DELETE FROM payout_history WHERE influencer_id = ?", [creator_id])
        cursor.execute("DELETE FROM affiliate_code WHERE influencer_id = ?", [creator_id])
        
        # Finally delete the influencer account
        cursor.execute("DELETE FROM influencers WHERE id = ?", [creator_id])
        
        conn.commit()
        
        # Clear session
        session.clear()
        
        return jsonify({"message": "Account deleted successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Delete Account Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

# --- Manager Profile/Settings API Routes ---

@app.route('/api/manager/profile', methods=['GET'])
@login_required(role='manager')
def get_manager_profile():
    """Gets the manager's profile information."""
    conn = None
    try:
        manager_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                id, company, log_email, ad_number, ad_email, location, 
                category, brand_description, camp_spend, camp_views
            FROM marketers 
            WHERE id = ?
        """, [manager_id])
        
        profile = cursor.fetchone()
        
        if not profile:
            return jsonify({"message": "Profile not found"}), 404
        
        return jsonify({
            "id": profile[0],
            "company": profile[1],
            "log_email": profile[2],
            "ad_number": profile[3],
            "ad_email": profile[4],
            "location": profile[5],
            "category": profile[6],
            "brand_description": profile[7],
            "camp_spend": profile[8],
            "camp_views": profile[9]
        }), 200
        
    except Exception as e:
        app.logger.error(f"Get Manager Profile Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/manager/profile', methods=['PUT'])
@login_required(role='manager')
def update_manager_profile():
    """Updates the manager's profile information."""
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        
        conn, cursor = get_db_connection()
        
        # Build update query dynamically
        allowed_fields = ['company', 'ad_number', 'ad_email', 'location', 
                         'category', 'brand_description', 'camp_spend', 'camp_views']
        update_fields = []
        values = []
        
        for field in allowed_fields:
            if field in data:
                update_fields.append(f"{field} = ?")
                values.append(data[field])
        
        if not update_fields:
            return jsonify({"message": "No fields to update"}), 400
        
        values.append(manager_id)
        query = f"UPDATE marketers SET {', '.join(update_fields)} WHERE id = ?"
        
        cursor.execute(query, values)
        conn.commit()
        
        return jsonify({"message": "Profile updated successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Update Manager Profile Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/manager/change-password', methods=['PUT'])
@login_required(role='manager')
def change_manager_password():
    """Changes the manager's password."""
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        
        if not current_password or not new_password:
            return jsonify({"message": "Current and new password are required"}), 400
        
        if len(new_password) < 8:
            return jsonify({"message": "New password must be at least 8 characters"}), 400
        
        conn, cursor = get_db_connection()
        
        # Verify current password
        cursor.execute("SELECT password FROM marketers WHERE id = ?", [manager_id])
        result = cursor.fetchone()
        
        if not result or not check_password_hash(result[0], current_password):
            return jsonify({"message": "Current password is incorrect"}), 401
        
        # Update to new password
        hashed_new_password = hash_password(new_password)
        cursor.execute("UPDATE marketers SET password = ? WHERE id = ?", 
                      [hashed_new_password, manager_id])
        conn.commit()
        
        return jsonify({"message": "Password changed successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Change Manager Password Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/manager/delete-account', methods=['DELETE'])
@login_required(role='manager')
def delete_manager_account():
    """Deletes the manager's account and all associated data."""
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        
        password = data.get('password')
        confirmation = data.get('confirmation')
        
        if not password:
            return jsonify({"message": "Password is required"}), 400
        
        if confirmation != "DELETE":
            return jsonify({"message": "Please type DELETE to confirm"}), 400
        
        conn, cursor = get_db_connection()
        
        # Verify password
        cursor.execute("SELECT password FROM marketers WHERE id = ?", [manager_id])
        result = cursor.fetchone()
        
        if not result or not check_password_hash(result[0], password):
            return jsonify({"message": "Incorrect password"}), 401
        
        # Delete associated campaigns and their data
        cursor.execute("SELECT id FROM campaigns WHERE manager_id = ?", [manager_id])
        campaign_ids = [row[0] for row in cursor.fetchall()]
        
        for campaign_id in campaign_ids:
            cursor.execute("DELETE FROM applications WHERE campaign_id = ?", [campaign_id])
            cursor.execute("DELETE FROM submitted_posts WHERE campaign_id = ?", [campaign_id])
            cursor.execute("DELETE FROM post_performance WHERE campaign_id = ?", [campaign_id])
        
        cursor.execute("DELETE FROM campaigns WHERE manager_id = ?", [manager_id])
        cursor.execute("DELETE FROM affiliate_campaigns WHERE manager_id = ?", [manager_id])
        
        # Finally delete the manager account
        cursor.execute("DELETE FROM marketers WHERE id = ?", [manager_id])
        
        conn.commit()
        
        # Clear session
        session.clear()
        
        return jsonify({"message": "Account deleted successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Delete Manager Account Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/affiliate-campaigns/<int:campaign_id>/codes', methods=['GET'])
@login_required(role='manager')
def get_affiliate_campaign_codes(campaign_id):
    """Gets all affiliate codes for a specific campaign with influencer details."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # Verify campaign belongs to manager
        cursor.execute("""
            SELECT id FROM affiliate_campaigns 
            WHERE id = ? AND manager_id = ?
        """, [campaign_id, manager_id])
        
        if not cursor.fetchone():
            return jsonify({"message": "Campaign not found or unauthorized"}), 404
        
        # Get all affiliate codes with influencer details
        cursor.execute("""
            SELECT 
                ac.id,
                ac.affiliate_code,
                ac.use_count,
                i.name as influencer_name,
                i.instagram,
                afc.flat_fee
            FROM affiliate_code ac
            JOIN influencers i ON ac.influencer_id = i.id
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE ac.campaign_id = ?
        """, [campaign_id])
        
        codes = cursor.fetchall()
        
        codes_list = []
        for code in codes:
            codes_list.append({
                'id': code[0],
                'affiliate_code': code[1],
                'use_count': code[2] or 0,
                'influencer_name': code[3],
                'instagram': code[4],
                'flat_fee': float(code[5] or 0)
            })
        
        return jsonify({'codes': codes_list})
        
    except Exception as e:
        app.logger.error(f"Error fetching affiliate codes: {e}")
        return jsonify({"message": "Internal server error"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/affiliate-codes/<int:code_id>', methods=['DELETE'])
@login_required(role='manager')
def delete_affiliate_code(code_id):
    """Deletes an affiliate code."""
    conn = None
    try:
        manager_id = session['user_id']
        conn, cursor = get_db_connection()
        
        # Verify the code belongs to a campaign owned by this manager
        cursor.execute("""
            SELECT ac.id, ac.campaign_id
            FROM affiliate_code ac
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE ac.id = ? AND afc.manager_id = ?
        """, [code_id, manager_id])
        
        code = cursor.fetchone()
        if not code:
            return jsonify({"message": "Affiliate code not found or unauthorized"}), 404
        
        # Delete the affiliate code
        cursor.execute("DELETE FROM affiliate_code WHERE id = ?", [code_id])
        
        # Also delete associated clicks if the table exists
        try:
            cursor.execute("DELETE FROM affiliate_clicks WHERE affiliate_code_id = ?", [code_id])
        except:
            pass  # Table might not exist
        
        conn.commit()
        
        return jsonify({"message": "Affiliate code deleted successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Error deleting affiliate code: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "Internal server error"}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/creator/affiliate-codes', methods=['GET'])
@login_required(role='creator')
def get_user_affiliate_codes():
    """Get all affiliate codes for the logged-in creator, including performance and fee."""
    conn = None
    try:
        creator_id = session['user_id']
        
        conn, cursor = get_db_connection()
        
        # Explicitly select only the fields needed for the frontend, including use_count and flat_fee
        cursor.execute('''
            SELECT 
                ac.affiliate_code,
                ac.use_count,
                afc.product_name,
                afc.brand,
                afc.flat_fee
            FROM affiliate_code ac
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE ac.influencer_id = ?
        ''', (creator_id,))
        
        codes = cursor.fetchall()
        
        # Convert rows to dictionaries properly
        codes_list = []
        # The column names are implicitly defined by the SELECT statement
        columns = ['affiliate_code', 'use_count', 'product_name', 'brand', 'flat_fee']
        
        for row in codes:
            code_dict = dict(zip(columns, row))
            codes_list.append(code_dict)
        
        conn.close()
        return jsonify(codes_list)
        
    except Exception as e:
        app.logger.error(f"Error fetching affiliate codes: {e}")
        return jsonify({"message": "Internal server error"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/track/<affiliate_code>', methods=['GET'])
def track_affiliate_link(affiliate_code):
    """Track affiliate link clicks and redirect to product URL."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # Get affiliate code details with campaign information
        cursor.execute('''
            SELECT 
                ac.id as code_id,
                ac.campaign_id,
                ac.product_link,
                ac.use_count,
                afc.product_name,
                afc.brand,
                afc.status as campaign_status
            FROM affiliate_code ac
            JOIN affiliate_campaigns afc ON ac.campaign_id = afc.id
            WHERE ac.affiliate_code = ?
        ''', (affiliate_code,))
        
        code_info = cursor.fetchone()
        
        if not code_info:
            conn.close()
            return jsonify({"message": "Invalid affiliate code"}), 404
        
        # Convert to dictionary for easier access
        code_info_dict = dict(zip([col[0] for col in cursor.description], code_info))
        
        # Check if campaign is still active
        if code_info_dict['campaign_status'] != 'live':
            conn.close()
            return jsonify({"message": "This affiliate campaign is no longer active"}), 410
        
        # Increment use count
        new_use_count = (code_info_dict['use_count'] or 0) + 1
        
        cursor.execute(
            'UPDATE affiliate_code SET use_count = ? WHERE id = ?',
            (new_use_count, code_info_dict['code_id'])
        )
        
        # Log the click for analytics (optional but recommended)
        try:
            cursor.execute('''
                INSERT INTO affiliate_clicks 
                (affiliate_code_id, clicked_at, ip_address, user_agent, referrer)
                VALUES (?, datetime('now'), ?, ?, ?)
            ''', (
                code_info_dict['code_id'],
                request.remote_addr,
                request.headers.get('User-Agent', ''),
                request.headers.get('Referer', '')
            ))
        except Exception as e:
            # If affiliate_clicks table doesn't exist, just continue
            app.logger.warning(f"Could not log affiliate click: {e}")
        
        conn.commit()
        conn.close()
        
        # Get the product link from the affiliate_code table
        product_link = code_info_dict['product_link']
        
        if product_link == url_for('joinswop', _external=True) or product_link == '/join-swop':
            # This is an affiliate link designed to track signups.
            # We redirect to the join-swop page WITH the affiliate_code as a query param.
            # This is what the JS on the page will read.
            return redirect(url_for('joinswop', affiliate_code=affiliate_code), code=302)
        else:
            # Normal product link redirect
            if not product_link.startswith(('http://', 'https://')):
                product_link = 'https://' + product_link
            return redirect(product_link, code=302)
        
    except Exception as e:
        # ... (Error handling)
        return jsonify({"message": "Internal server error tracking affiliate link"}), 500
        
    except Exception as e:
        app.logger.error(f"Error tracking affiliate link: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"message": "Internal server error tracking affiliate link"}), 500
    
@app.route('/api/creator/generate-affiliate-code', methods=['POST'])
@login_required(role='creator')
def generate_affiliate_code():
    """Generate a new affiliate code for a campaign."""
    conn = None
    try:
        creator_id = session['user_id']
        data = request.get_json()
        campaign_id = data.get('campaign_id')
        
        if not campaign_id:
            return jsonify({"message": "Campaign ID is required"}), 400
        
        conn, cursor = get_db_connection()
        
        # Check if campaign exists and is live, and count current influencers
        cursor.execute('''
            SELECT 
                ac.*, 
                COUNT(af.id) as current_influencers
            FROM affiliate_campaigns ac
            LEFT JOIN affiliate_code af ON ac.id = af.campaign_id
            WHERE ac.id = ? AND ac.status = 'live'
            GROUP BY ac.id
        ''', (campaign_id,))
        
        campaign_row = cursor.fetchone()
        
        if not campaign_row:
            conn.close()
            return jsonify({"message": "Campaign not found or not active"}), 404
        
        # Convert row to dictionary properly
        columns = [col[0] for col in cursor.description]
        campaign_dict = dict(zip(columns, campaign_row))
        
        # Check if campaign has available slots using the actual count
        current_influencers = campaign_dict['current_influencers'] or 0
        max_influencers = campaign_dict['max_influencers'] or 0
        
        if current_influencers >= max_influencers:
            conn.close()
            return jsonify({"message": "Campaign is full - maximum number of affiliate links reached"}), 400
        
        # Check if user already has an affiliate code for this campaign
        cursor.execute(
            'SELECT * FROM affiliate_code WHERE campaign_id = ? AND influencer_id = ?',
            (campaign_id, creator_id)
        )
        existing_code = cursor.fetchone()
        
        if existing_code:
            conn.close()
            return jsonify({"message": "You already have an affiliate code for this campaign"}), 400
        
        # Generate unique affiliate code
        affiliate_code = 'AFF' + str(uuid.uuid4().hex[:12]).upper()
        
        # Create tracking link
        base_url = request.host_url.rstrip('/')
        tracking_link = f"{base_url}/track/{affiliate_code}"
        
        # Get product URL from campaign
        product_link = campaign_dict['product_url']
        
        # Insert into database with product_link
        cursor.execute('''
            INSERT INTO affiliate_code (campaign_id, influencer_id, affiliate_code, product_link, use_count)
            VALUES (?, ?, ?, ?, ?)
        ''', (campaign_id, creator_id, affiliate_code, product_link, 0))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "affiliate_code": affiliate_code,
            "affiliate_link": tracking_link,
            "commission": float(campaign_dict['flat_fee']),
            "product_name": campaign_dict['product_name'],
            "brand": campaign_dict['brand'],
            "product_link": product_link
        })
        
    except Exception as e:
        print(f"Error generating affiliate code: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return jsonify({"message": "Internal server error"}), 500
        
@app.route('/api/affiliate-campaigns/create', methods=['POST'])
@login_required(role='manager')
def create_affiliate_campaign():
    """Creates a new affiliate marketing campaign."""
    conn = None
    cursor = None
    try:
        manager_id = session['user_id']
        data = request.get_json()
        
        required_fields = ['product_name', 'brand', 'product_url', 'kpi', 'max_influencers', 'flat_fee']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"message": f"Missing required field: {field}"}), 400
        
        # Validate numeric fields
        try:
            max_influencers = int(data['max_influencers'])
            flat_fee = float(data['flat_fee'])
            min_profile_score = int(data.get('min_profile_score', 50))
        except (ValueError, TypeError):
            return jsonify({"message": "Invalid numeric values"}), 400
        
        if max_influencers <= 0 or flat_fee <= 0 or min_profile_score < 0 or min_profile_score > 100:
            return jsonify({"message": "Invalid numeric values"}), 400
        
        # FIX: Get both connection and cursor
        conn, cursor = get_db_connection()
        
        # FIX: Use cursor.execute() instead of conn.execute()
        cursor.execute('''
            INSERT INTO affiliate_campaigns (
                manager_id, product_name, product_description, brand, product_url,
                kpi, max_influencers, flat_fee, min_profile_score, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?)
        ''', (
            manager_id,
            data['product_name'],
            data.get('product_description', ''),
            data['brand'],
            data['product_url'],
            data['kpi'],
            max_influencers,
            flat_fee,
            min_profile_score,
            datetime.now().isoformat(),
            datetime.now().isoformat()
        ))
        
        # FIX: Use cursor.lastrowid instead of conn.lastrowid
        campaign_id = cursor.lastrowid
        conn.commit()
        
        return jsonify({
            "message": "Affiliate campaign created successfully",
            "campaign_id": campaign_id
        })
        
    except Exception as e:
        print(f"Error creating affiliate campaign: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        if conn:
            conn.rollback()
        return jsonify({"message": "Internal server error"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator-stats')
def get_creator_stats():
    # FIX: Initialize conn and cursor correctly to None
    conn = cursor = None
    try:
        # Establish connection and get cursor
        conn, cursor = get_db_connection()
        
        # SQL Query to get total, average, and premium creators count
        cursor.execute("""
            SELECT 
                COUNT(*) as total_creators,
                COALESCE(AVG(profile_score), 0) as avg_score,
                COUNT(CASE WHEN profile_score >= 80 THEN 1 END) as premium_creators
            FROM influencers
        """)
        
        # Fetch the single row result
        result = cursor.fetchone()
        
        if result:
            total_creators = int(result[0])
            avg_score = round(float(result[1]), 2)
            premium_creators = int(result[2])
        else:
            # Fallback for empty table
            total_creators, avg_score, premium_creators = 0, 0.00, 0

        return jsonify({
            "total_creators": total_creators,
            "average_profile_score": avg_score,
            "premium_creators": premium_creators
        }), 200
        
    except Exception as e:
        app.logger.error(f"Creator Stats Error: {e}")
        return jsonify({"message": "An internal server error occurred fetching creator stats"}), 500
    finally:
        # Ensure the connection is closed
        if conn:
            conn.close()

@app.route('/api/campaigns/create', methods=['POST'])
@login_required(role='manager')
def create_campaign():
    """Creates a new campaign for the logged-in manager."""
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        
        # ... [Existing validation logic] ...
        required_fields = ['title', 'client_company', 'description', 'categories', 
                          'platforms', 'geographic_restriction', 'vetting_score',
                          'total_budget', 'compensation_model', 'start_date', 'end_date']
        
        for field in required_fields:
            if field not in data or data[field] is None:
                return jsonify({"message": f"Missing required field: {field}"}), 400
        
        conn, cursor = get_db_connection()
        
        # ... [Existing data processing logic] ...
        design_brief_files = json.dumps(data.get('design_brief_files', [])) if data.get('design_brief_files') else None
        categories = json.dumps(data.get('categories', []))
        platforms = json.dumps(data.get('platforms', []))
        
        age_min = int(data.get('age_min', 0)) if data.get('age_min') else None
        age_max = int(data.get('age_max', 0)) if data.get('age_max') else None
        vetting_score = int(data.get('vetting_score', 0))
        total_budget = float(data.get('total_budget', 0))
        cpm_rate = float(data.get('cpm_rate', 0)) if data.get('cpm_rate') else None
        budget_cap_percentage = int(data.get('budget_cap_percentage', 0)) if data.get('budget_cap_percentage') else None
        
        budget_cap_amount = None
        if budget_cap_percentage and total_budget:
            budget_cap_amount = total_budget * budget_cap_percentage / 100
        
        compliance_checked = 1 if data.get('compliance_checked') else 0
        current_timestamp = datetime.now().isoformat()
        
        # Insert Campaign
        query = """
            INSERT INTO campaigns 
            (manager_id, title, client_company, description, design_brief_files, 
             categories, age_min, age_max, gender_split, platforms, geographic_restriction, 
             vetting_score, total_budget, compensation_model, cpm_rate, 
             budget_cap_percentage, budget_cap_amount, start_date, end_date, 
             status, compliance_checked, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        values = [
            manager_id, data['title'], data['client_company'], data['description'], design_brief_files,
            categories, age_min, age_max, data.get('gender_split'), platforms,
            data['geographic_restriction'], vetting_score, total_budget,
            data['compensation_model'], cpm_rate, budget_cap_percentage,
            budget_cap_amount, data['start_date'], data['end_date'],
            data.get('status', 'draft'), compliance_checked, current_timestamp, current_timestamp
        ]
        
        cursor.execute(query, values)
        campaign_id = cursor.lastrowid

        # Handle Shortlist
        shortlisted_influencers = data.get('shortlisted_influencers', [])
        update_campaign_shortlist(cursor, campaign_id, shortlisted_influencers)

        # --- NEW: Track T&C Acceptance ---
        if data.get('tnc_accepted') and data.get('status') == 'live':
            track_tnc_acceptance(cursor, manager_id, role='manager')
        # ---------------------------------

        conn.commit()
        
        return jsonify({
            "message": "Campaign created successfully",
            "campaign_id": campaign_id
        }), 201
        
    except Exception as e:
        app.logger.error(f"Campaign Creation Error: {e}")
        if conn: conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/campaigns/<int:campaign_id>/status', methods=['PUT'])
@login_required(role='manager')
def update_campaign_status(campaign_id):
    """
    Updates the status of a specific campaign (e.g., to 'completed').
    This is used for the 'Mark Inactive' button.
    """
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        # We enforce 'completed' for the 'Mark Inactive' button's functionality
        new_status = data.get('status', 'completed') 
        
        if new_status not in ['live', 'paused', 'completed', 'draft']:
            return jsonify({"message": "Invalid campaign status"}), 400

        conn, cursor = get_db_connection()
        
        # Ensure the manager owns the campaign
        cursor.execute("SELECT id FROM campaigns WHERE id = ? AND manager_id = ?", [campaign_id, manager_id])
        if cursor.fetchone() is None:
            return jsonify({"message": "Campaign not found or unauthorized"}), 404
            
        cursor.execute("""
            UPDATE campaigns
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [new_status, campaign_id])
        
        conn.commit()
        return jsonify({"message": f"Campaign {campaign_id} status updated to {new_status}"}), 200
        
    except Exception as e:
        app.logger.error(f"Update Campaign Status Error: {e}")
        if conn: conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/campaigns/<int:campaign_id>', methods=['PUT'])
@login_required(role='manager')
def update_campaign(campaign_id):
    """Updates an existing campaign (used for saving/resuming drafts)."""
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        
        conn, cursor = get_db_connection()
        
        # 1. Verify ownership
        cursor.execute("SELECT id FROM campaigns WHERE id = ? AND manager_id = ?", [campaign_id, manager_id])
        if not cursor.fetchone():
            return jsonify({"message": "Campaign not found or unauthorized"}), 404

        # ... [Keep existing data processing logic] ...
        design_brief_files = json.dumps(data.get('design_brief_files', [])) if data.get('design_brief_files') else None
        categories = json.dumps(data.get('categories', []))
        platforms = json.dumps(data.get('platforms', []))
        
        age_min = int(data.get('age_min', 0)) if data.get('age_min') else None
        age_max = int(data.get('age_max', 0)) if data.get('age_max') else None
        vetting_score = int(data.get('vetting_score', 0))
        total_budget = float(data.get('total_budget', 0))
        cpm_rate = float(data.get('cpm_rate', 0)) if data.get('cpm_rate') else None
        budget_cap_percentage = int(data.get('budget_cap_percentage', 0)) if data.get('budget_cap_percentage') else None
        
        budget_cap_amount = None
        if budget_cap_percentage and total_budget:
            budget_cap_amount = total_budget * budget_cap_percentage / 100
            
        compliance_checked = 1 if data.get('compliance_checked') else 0
        current_timestamp = datetime.now().isoformat()

        # Update Query
        query = """
            UPDATE campaigns 
            SET title=?, client_company=?, description=?, design_brief_files=?, 
                categories=?, age_min=?, age_max=?, gender_split=?, platforms=?, 
                geographic_restriction=?, vetting_score=?, total_budget=?, 
                compensation_model=?, cpm_rate=?, budget_cap_percentage=?, 
                budget_cap_amount=?, start_date=?, end_date=?, status=?, 
                compliance_checked=?, updated_at=?
            WHERE id=? AND manager_id=?
        """
        
        values = [
            data.get('title'), data.get('client_company'), data.get('description'), design_brief_files,
            categories, age_min, age_max, data.get('gender_split'), platforms,
            data.get('geographic_restriction'), vetting_score, total_budget,
            data.get('compensation_model'), cpm_rate, budget_cap_percentage,
            budget_cap_amount, data.get('start_date'), data.get('end_date'),
            data.get('status', 'draft'), compliance_checked, current_timestamp,
            campaign_id, manager_id
        ]
        
        cursor.execute(query, values)

        # Handle Shortlist
        shortlisted_influencers = data.get('shortlisted_influencers', [])
        update_campaign_shortlist(cursor, campaign_id, shortlisted_influencers)

        # --- NEW: Track T&C Acceptance ---
        if data.get('tnc_accepted') and data.get('status') == 'live':
            track_tnc_acceptance(cursor, manager_id, role='manager')
        # ---------------------------------

        conn.commit()
        
        return jsonify({"message": "Campaign updated successfully", "campaign_id": campaign_id}), 200

    except Exception as e:
        app.logger.error(f"Campaign Update Error: {e}")
        if conn: conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn: conn.close()
        
@app.route('/api/campaigns/<int:campaign_id>/applications', methods=['GET'])
@login_required(role='manager')
def get_campaign_applications_and_posts(campaign_id):
    """
    Fetches all applications (pending/accepted/rejected) and approved posts for a campaign.
    """
    conn = None
    try:
        manager_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # 1. Check ownership
        cursor.execute("SELECT id FROM campaigns WHERE id = ? AND manager_id = ?", [campaign_id, manager_id])
        if cursor.fetchone() is None:
            return jsonify({"message": "Campaign not found or unauthorized"}), 404
            
        # 2. Fetch Applications (Query remains the same as it was correct)
        applications_query = """
            SELECT 
                a.id AS application_id, 
                a.status AS application_status, 
                a.applied_at,
                i.id AS influencer_id,
                i.name, 
                i.email, 
                i.instagram, 
                i.tik_tok, 
                i.youtube,
                i.avg_views,
                i.reach,
                i.profile_score,
                a.application_data
            FROM applications a
            JOIN influencers i ON a.influencer_id = i.id
            WHERE a.campaign_id = ?
            ORDER BY a.status, a.applied_at DESC
        """
        cursor.execute(applications_query, [campaign_id])
        applications = []
        for row in cursor.fetchall():
            app_data = dict(zip([col[0] for col in cursor.description], row))
            app_data['application_data'] = json.loads(app_data['application_data']) if app_data.get('application_data') else {}
            applications.append(app_data)

        # 3. Fetch Approved Posts (FIXED: replaced posted_date with submitted_at)
        posts_query = """
            SELECT 
                sp.id AS post_id,
                sp.post_url,
                sp.status AS post_status,
                sp.platform,
                sp.views,
                sp.engagement,
                sp.earnings,
                sp.submitted_at, -- CORRECTED COLUMN NAME
                i.name AS influencer_name
            FROM submitted_posts sp
            JOIN influencers i ON sp.influencer_id = i.id
            WHERE sp.campaign_id = ? AND sp.status IN ('approved', 'paid')
            ORDER BY sp.submitted_at DESC -- CORRECTED COLUMN NAME
        """
        cursor.execute(posts_query, [campaign_id])
        approved_posts = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
        
        return jsonify({
            "applications": applications, 
            "approved_posts": approved_posts
        }), 200
        
    except Exception as e:
        app.logger.error(f"Get Campaign Applications Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/applications/<int:application_id>/status', methods=['PUT'])
@login_required(role='manager')
def update_application_status(application_id):
    """
    Updates the status of a specific application (to 'accepted' or 'rejected').
    """
    conn = None
    try:
        manager_id = session.get('user_id')
        data = request.get_json()
        new_status = data.get('status')
        
        if new_status not in ['accepted', 'rejected']:
            return jsonify({"message": "Invalid application status"}), 400

        conn, cursor = get_db_connection()
        
        # 1. Get campaign_id and check ownership via campaign_id
        cursor.execute("""
            SELECT a.campaign_id 
            FROM applications a
            JOIN campaigns c ON a.campaign_id = c.id
            WHERE a.id = ? AND c.manager_id = ?
        """, [application_id, manager_id])
        
        campaign_row = cursor.fetchone()
        if campaign_row is None:
            return jsonify({"message": "Application not found or unauthorized"}), 404
            
        # 2. Update application status
        cursor.execute("""
            UPDATE applications
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [new_status, application_id])
        
        conn.commit()
        return jsonify({"message": f"Application {application_id} status updated to {new_status}"}), 200
        
    except Exception as e:
        app.logger.error(f"Update Application Status Error: {e}")
        if conn: conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/campaigns/my-campaigns', methods=['GET'])
@login_required(role='manager')
def get_my_campaigns():
    """Gets all campaigns for the logged-in manager."""
    conn = None
    try:
        manager_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT id, title, client_company, status, total_budget, 
                   start_date, end_date, created_at, vetting_score, platforms
            FROM campaigns 
            WHERE manager_id = ?
            ORDER BY created_at DESC
        """, [manager_id])
        
        campaigns = []
        for row in cursor.fetchall():
            platforms = []
            try:
                platforms = json.loads(row[9]) if row[9] else []
            except:
                platforms = []
            
            campaigns.append({
                "id": row[0],
                "title": row[1],
                "client_company": row[2],
                "status": row[3],
                "total_budget": float(row[4]) if row[4] else 0,
                "start_date": row[5],
                "end_date": row[6],
                "created_at": row[7],
                "vetting_score": row[8],
                "platforms": platforms
            })
        
        return jsonify({"campaigns": campaigns}), 200
        
    except Exception as e:
        app.logger.error(f"Get Campaigns Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/campaigns/<int:campaign_id>', methods=['GET'])
@login_required(role='manager')
def get_campaign(campaign_id):
    """Gets a specific campaign by ID."""
    conn = None
    try:
        manager_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        cursor.execute("SELECT * FROM campaigns WHERE id = ? AND manager_id = ?", [campaign_id, manager_id])
        campaign_row = cursor.fetchone()
        
        if not campaign_row:
            return jsonify({"message": "Campaign not found"}), 404
        
        # ... [Keep existing parsing logic] ...
        cursor.execute("PRAGMA table_info(campaigns)")
        columns = [column[1] for column in cursor.fetchall()]
        campaign = dict(zip(columns, campaign_row))
        
        if campaign.get('design_brief_files'):
            try: campaign['design_brief_files'] = json.loads(campaign['design_brief_files'])
            except: campaign['design_brief_files'] = []
        
        if campaign.get('categories'):
            try: campaign['categories'] = json.loads(campaign['categories'])
            except: campaign['categories'] = []
        
        if campaign.get('platforms'):
            try: campaign['platforms'] = json.loads(campaign['platforms'])
            except: campaign['platforms'] = []
        
        if campaign.get('total_budget'): campaign['total_budget'] = float(campaign['total_budget'])
        if campaign.get('cpm_rate'): campaign['cpm_rate'] = float(campaign['cpm_rate'])
        if campaign.get('budget_cap_amount'): campaign['budget_cap_amount'] = float(campaign['budget_cap_amount'])
        
        # --- NEW: Fetch Shortlist Details ---
        # --- NEW: Fetch Shortlist Details ---
        cursor.execute("""
            SELECT i.id, i.name, i.instagram, i.tik_tok, i.youtube, i.profile_score, i.category
            FROM campaign_shortlist cs
            JOIN influencers i ON cs.influencer_id = i.id
            WHERE cs.campaign_id = ?
        """, [campaign_id])

        shortlist = []
        for row in cursor.fetchall():
            shortlist.append({
                "id": row[0],
                "name": row[1],
                "instagram": row[2],
                "tik_tok": row[3],
                "youtube": row[4],
                "profile_score": row[5],
                "category": row[6]
            })

        campaign['shortlisted_influencers_details'] = shortlist
        campaign['shortlisted_influencers'] = [item['id'] for item in shortlist]
        # ------------------------------------
        
        return jsonify({"campaign": campaign}), 200
        
    except Exception as e:
        app.logger.error(f"Get Campaign Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/auth/verify', methods=['GET'])
def verify_session():
    """Verify if the current session is valid"""
    if 'user_id' in session:
        return jsonify({
            "valid": True,
            "role": session.get('role'),
            "user_id": session.get('user_id')
        }), 200
    else:
        return jsonify({"valid": False}), 401
    
@app.route('/api/campaigns/live', methods=['GET'])
@login_required(role='creator')
def get_live_campaigns():
    """Gets all live campaigns for creators to apply to with full details."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                c.id, c.title, c.client_company as brand, 
                m.company as agency, c.total_budget as budget,
                c.cpm_rate as cpm, c.platforms, c.categories as niche,
                c.geographic_restriction as location, c.vetting_score as match_rating,
                c.compensation_model as campaign_type, c.start_date, c.end_date,
                c.description, c.age_min, c.age_max, c.gender_split,
                c.design_brief_files, c.budget_cap_percentage, c.budget_cap_amount,
                c.compliance_checked, c.created_at, c.budget_used
            FROM campaigns c
            JOIN marketers m ON c.manager_id = m.id
            WHERE c.status = 'live'
            ORDER BY c.created_at DESC
        """)
        
        campaigns = []
        for row in cursor.fetchall():
            # Parse JSON fields
            # Index mapping:
            # 0: id, 1: title, 2: brand (client_company), 3: agency (company)
            # 4: budget (total_budget), 5: cpm (cpm_rate), 6: platforms, 7: niche (categories)
            # 8: location (geographic_restriction), 9: match_rating (vetting_score), 10: campaign_type (compensation_model)
            # 11: start_date, 12: end_date, 13: description
            # 14: age_min, 15: age_max, 16: gender_split, 17: design_brief_files
            # 18: budget_cap_percentage, 19: budget_cap_amount, 20: compliance_checked
            # 21: created_at, 22: budget_used
            
            platforms = []
            categories = []
            design_brief_files = []
            
            try:
                platforms = json.loads(row[6]) if row[6] else []
            except:
                platforms = []
            
            try:
                categories = json.loads(row[7]) if row[7] else []
            except:
                categories = []
            
            try:
                design_brief_files = json.loads(row[17]) if row[17] else []
            except:
                design_brief_files = []
            
            # Get first category as niche
            niche = categories[0] if categories else "General"
            
            # Get budget used from database (defaults to 0 if NULL)
            # row[22] is budget_used column
            budget_used = int(row[22]) if row[22] is not None else 0
            
            # Parse budget values
            budget = float(row[4]) if row[4] else 0
            cpm = float(row[5]) if row[5] else 0
            
            campaigns.append({
                "id": row[0],
                "title": row[1],
                "brand": row[2],
                "agency": row[3],
                "budget": budget,
                "cpm": cpm,
                "platforms": platforms,
                "niche": niche,
                "categories": categories,  # Full categories list
                "location": row[8],
                "match_rating": row[9],
                "campaign_type": row[10],
                "start_date": row[11],
                "end_date": row[12],
                "description": row[13],
                "age_min": row[14],
                "age_max": row[15],
                "gender_split": row[16],
                "design_brief_files": design_brief_files,
                "budget_cap_percentage": int(row[18]) if row[18] else None,
                "budget_cap_amount": float(row[19]) if row[19] else None,
                "compliance_checked": bool(row[20]),
                "created_at": row[21],
                "budget_used": budget_used
            })
        
        return jsonify(campaigns), 200
        
    except Exception as e:
        app.logger.error(f"Get Live Campaigns Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/apply', methods=['POST'])
@login_required(role='creator')
def apply_to_campaign():
    """Allows a creator to apply to a campaign."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        data = request.get_json()
        campaign_id = data.get('campaign_id')
        
        if not campaign_id:
            return jsonify({"message": "Campaign ID is required"}), 400
        
        conn, cursor = get_db_connection()
        
        # Check if campaign exists and is live
        cursor.execute("SELECT status FROM campaigns WHERE id = ?", [campaign_id])
        campaign = cursor.fetchone()
        
        if not campaign:
            return jsonify({"message": "Campaign not found"}), 404
        
        if campaign[0] != 'live':
            return jsonify({"message": "Campaign is not currently accepting applications"}), 400
        
        # Check if already applied - FIXED: More specific query
        cursor.execute("""
            SELECT id, status FROM applications 
            WHERE campaign_id = ? AND influencer_id = ?
        """, [campaign_id, influencer_id])
        existing_application = cursor.fetchone()
        
        if existing_application:
            app.logger.info(f"Existing application found: {existing_application}")
            return jsonify({"message": "You have already applied to this campaign"}), 400
        
        # Get influencer profile data
        cursor.execute("""
            SELECT name, instagram, tik_tok, youtube, avg_views, reach, email, number
            FROM influencers WHERE id = ?
        """, [influencer_id])
        
        influencer_data = cursor.fetchone()
        if not influencer_data:
            return jsonify({"message": "Influencer profile not found"}), 404
        
        # Prepare application data
        application_data = {
            "name": influencer_data[0],
            "instagram": influencer_data[1],
            "tik_tok": influencer_data[2],
            "youtube": influencer_data[3],
            "avg_views": influencer_data[4],
            "reach": influencer_data[5],
            "email": influencer_data[6],
            "number": influencer_data[7],
            "applied_at": datetime.now().isoformat()
        }
        
        # Insert application
        current_time = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO applications 
            (campaign_id, influencer_id, application_data, status, applied_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
        """, [campaign_id, influencer_id, json.dumps(application_data), current_time, current_time])
        
        conn.commit()
        
        # Verify the application was created
        cursor.execute("SELECT id FROM applications WHERE campaign_id = ? AND influencer_id = ?", 
                      [campaign_id, influencer_id])
        new_application = cursor.fetchone()
        
        if not new_application:
            return jsonify({"message": "Failed to create application"}), 500
            
        app.logger.info(f"Application created successfully: {new_application[0]}")
        return jsonify({"message": "Application submitted successfully"}), 201
        
    except Exception as e:
        error_str = str(e).lower()
        app.logger.error(f"Apply to Campaign Error: {e}")
        
        if 'unique constraint failed' in error_str or 'duplicate' in error_str:
            return jsonify({"message": "You have already applied to this campaign"}), 400
        else:
            if conn:
                conn.rollback()
            return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/applications', methods=['GET'])
@login_required(role='creator')
def get_creator_applications():
    """Gets all applications for the logged-in creator."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT campaign_id, status, applied_at
            FROM applications 
            WHERE influencer_id = ?
            ORDER BY applied_at DESC
        """, [influencer_id])
        
        applications = []
        for row in cursor.fetchall():
            applications.append({
                "campaign_id": row[0],
                "status": row[1] or 'pending',  # Handle NULL status
                "applied_at": row[2]
            })
        
        return jsonify(applications), 200
        
    except Exception as e:
        app.logger.error(f"Get Creator Applications Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/financial-stats', methods=['GET'])
@login_required(role='creator')
def get_creator_financial_stats():
    """Gets creator financial statistics from the influencer table."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # Fetch financial data from influencer table
        cursor.execute("""
            SELECT total_earnings, current_balance, profile_score 
            FROM influencers 
            WHERE id = ?
        """, [influencer_id])
        
        result = cursor.fetchone()
        
        if not result:
            return jsonify({"message": "Influencer profile not found"}), 404
        
        # Extract values, default to 0 if None
        total_earnings = result[0] or 0
        current_balance = result[1] or 0
        profile_score = result[2] or 0
        
        return jsonify({
            "total_earnings": total_earnings,
            "current_balance": current_balance,
            "profile_score": profile_score
        }), 200
        
    except Exception as e:
        app.logger.error(f"Creator Financial Stats Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

# Add these endpoints to your app.py file

# app.py

# app.py

# ... other imports and functions ...

@app.route('/api/creator/my-campaigns', methods=['GET'])
@login_required(role='creator')
def get_creator_campaigns():
    """Gets all campaigns the creator has applied to with their application status."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        conn, cursor = get_db_connection()

        # SQL Query: Join applications (a) with campaigns (c) and marketers (m)
        query = """
            SELECT 
                a.status AS application_status,
                c.id, 
                c.title, 
                c.description, 
                c.client_company,
                c.total_budget,
                c.cpm_rate,
                c.compensation_model,
                c.start_date,
                c.end_date,
                c.platforms,
                c.categories,
                m.company AS agency,
                c.budget_used
            FROM applications a
            JOIN campaigns c ON a.campaign_id = c.id
            JOIN marketers m ON c.manager_id = m.id
            WHERE a.influencer_id = ?
            ORDER BY a.applied_at DESC
        """
        cursor.execute(query, [influencer_id])
        
        campaigns = []
        
        for row in cursor.fetchall():
            # Parse JSON fields
            platforms = []
            try:
                platforms = json.loads(row[10]) if row[10] else [] # platforms is now index 10
            except:
                platforms = []

            categories = []
            try:
                categories = json.loads(row[11]) if row[11] else [] # categories is now index 11
            except:
                categories = []

            # Calculate days remaining
            days_remaining = 0
            if row[9]: # end_date is now index 9
                try:
                    end_date = datetime.fromisoformat(row[9])
                    days_remaining = max(0, (end_date - datetime.now()).days)
                except:
                    days_remaining = 0

            # Get post statistics for the current campaign and influencer
            # This is important for showing posts submitted and earnings
            cursor.execute("""
                SELECT 
                    COUNT(id), 
                    SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END),
                    COALESCE(SUM(earnings), 0)
                FROM submitted_posts 
                WHERE influencer_id = ? AND campaign_id = ?
            """, [influencer_id, row[1]]) # row[1] is c.id (campaign_id)
            
            post_stats = cursor.fetchone()
            posts_count = post_stats[0]
            paid_posts = post_stats[1]
            total_earned = float(post_stats[2])
            
            # Budget Calculations
            total_budget = float(row[5]) if row[5] else 0.00 # index 5
            budget_used = float(row[13]) if row[13] else 0.00 # index 13
            budget_remaining = max(0.00, total_budget - budget_used)
            budget_used_percentage = (budget_used / total_budget * 100) if total_budget > 0 else 0.00
            
            campaigns.append({
                "application_status": row[0],
                "campaign_id": row[1],
                "title": row[2],
                "description": row[3],
                "client_company": row[4],
                "total_budget": round(total_budget, 2),
                "budget_used": round(budget_used, 2), 
                "budget_remaining": round(budget_remaining, 2),
                "budget_used_percentage": round(budget_used_percentage, 2),
                "cpm_rate": float(row[6]) if row[6] else 0.00,
                "compensation_model": row[7],
                "start_date": row[8],
                "end_date": row[9],
                "platforms": platforms,
                "categories": categories,
                "agency": row[12],
                "days_remaining": days_remaining,
                "posts_submitted": posts_count,
                "paid_posts": paid_posts,
                "total_earned": round(total_earned, 2)
            })
        
        return jsonify({"campaigns": campaigns}), 200

    except Exception as e:
        app.logger.error(f"Get Creator Campaigns Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/submit-post', methods=['POST'])
@login_required(role='creator')
def submit_post():
    """Submit a post link for an approved campaign, including initial analytics data."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        data = request.get_json()
        
        campaign_id = data.get('campaign_id')
        platform = data.get('platform')
        post_url = data.get('post_url')
        post_description = data.get('post_description', '')
        
        views = data.get('views', 0)
        engagement = data.get('engagement', 0)
        
        if not campaign_id or not platform or not post_url:
            return jsonify({"message": "Missing required fields"}), 400
        
        conn, cursor = get_db_connection()
        
        # Verify the creator is approved for this campaign
        cursor.execute("""
            SELECT status FROM applications 
            WHERE campaign_id = ? AND influencer_id = ?
        """, [campaign_id, influencer_id])
        
        application = cursor.fetchone()
        
        if not application:
            return jsonify({"message": "You have not applied to this campaign"}), 404
        
        # FIXED: Accept both 'approved' and 'accepted' statuses
        if application[0] not in ['approved', 'accepted', 'drafting_content']:
            return jsonify({"message": "You must be approved for this campaign to submit posts"}), 403
        
        # Insert the submitted post - UPDATED to include views and engagement
        current_time = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO submitted_posts 
            (campaign_id, influencer_id, platform, post_url, post_description, 
             views, engagement, status, submitted_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, ?, ?)
        """, [campaign_id, influencer_id, platform, post_url, post_description, 
              views, engagement, # <-- INSERTED VIEWS AND ENGAGEMENT HERE
              current_time, current_time, current_time])
        
        post_id = cursor.lastrowid
        
        # Insert initial post_performance record (used for creator analytics)
        cursor.execute("""
            INSERT INTO post_performance 
            (influencer_id, campaign_id, post_title, platform, post_url, 
             total_views, total_interactions, earnings, status, posted_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, date('now'), ?)
        """, [influencer_id, campaign_id, post_description or f"Post for campaign {campaign_id}", 
              platform, post_url, views, engagement, 0.00, 'Pending Review', current_time])

        
        # Update application status to drafting_content if it's still approved
        if application[0] == 'approved':
            cursor.execute("""
                UPDATE applications 
                SET status = 'drafting_content', updated_at = ?
                WHERE campaign_id = ? AND influencer_id = ?
            """, [current_time, campaign_id, influencer_id])
        
        conn.commit()
        
        return jsonify({
            "message": "Post submitted successfully",
            "post_id": post_id
        }), 201
        
    except Exception as e:
        error_str = str(e).lower()
        if 'unique constraint failed' in error_str:
            return jsonify({"message": "You have already submitted this post"}), 400
        else:
            app.logger.error(f"Submit Post Error: {e}")
            if conn:
                conn.rollback()
            return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/campaign-posts/<int:campaign_id>', methods=['GET'])
@login_required(role='creator')
def get_campaign_posts(campaign_id):
    """Get all submitted posts for a specific campaign."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                id, platform, post_url, post_description, 
                status, submitted_at, views, engagement, 
                earnings, reviewed_at, feedback, created_at, updated_at
            FROM submitted_posts
            WHERE campaign_id = ? AND influencer_id = ?
            ORDER BY submitted_at DESC
        """, [campaign_id, influencer_id])
        
        posts = []
        for row in cursor.fetchall():
            posts.append({
                "id": row[0],
                "platform": row[1],
                "post_url": row[2],
                "post_description": row[3],
                "status": row[4],
                "submitted_at": row[5],
                "views": row[6] or 0,
                "engagement": row[7] or 0,
                "earnings": float(row[8]) if row[8] else 0,
                "reviewed_at": row[9],
                "feedback": row[10],
                "created_at": row[11],
                "updated_at": row[12]
            })
        
        return jsonify(posts), 200
        
    except Exception as e:
        app.logger.error(f"Get Campaign Posts Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/analytics-data', methods=['GET'])
@login_required(role='creator')
def get_creator_analytics_data():
    """Gets comprehensive analytics data for the creator dashboard including all performance metrics."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # Get financial summary from creator_financials table
        cursor.execute("""
            SELECT total_earnings, current_balance, pending_earnings, average_cpm
            FROM creator_financials 
            WHERE influencer_id = ?
        """, [influencer_id])
        
        financials = cursor.fetchone()
        
        app.logger.info(f"Financials from creator_financials: {financials}")
        
        # If no financial record exists, calculate from submitted_posts
        if not financials:
            # Calculate total earnings from paid posts
            cursor.execute("""
                SELECT COALESCE(SUM(earnings), 0) 
                FROM submitted_posts 
                WHERE influencer_id = ? AND status = 'paid'
            """, [influencer_id])
            total_earnings_result = cursor.fetchone()
            total_earnings = float(total_earnings_result[0]) if total_earnings_result and total_earnings_result[0] else 0.00
            
            # Calculate pending earnings from approved posts
            cursor.execute("""
                SELECT COALESCE(SUM(earnings), 0) 
                FROM submitted_posts 
                WHERE influencer_id = ? AND status = 'approved'
            """, [influencer_id])
            pending_result = cursor.fetchone()
            pending_earnings = float(pending_result[0]) if pending_result and pending_result[0] else 0.00
            
            # Calculate current balance (total earnings minus payouts)
            cursor.execute("""
                SELECT COALESCE(SUM(amount), 0) 
                FROM payout_history 
                WHERE influencer_id = ? AND status = 'Completed'
            """, [influencer_id])
            payouts_result = cursor.fetchone()
            total_payouts = float(payouts_result[0]) if payouts_result and payouts_result[0] else 0.00
            current_balance = total_earnings - total_payouts
            
            # Calculate average CPM from paid posts
            cursor.execute("""
                SELECT 
                    COALESCE(AVG(CASE WHEN views > 0 THEN (earnings / views) * 1000 ELSE 0 END), 0)
                FROM submitted_posts 
                WHERE influencer_id = ? AND status = 'paid' AND earnings > 0 AND views > 0
            """, [influencer_id])
            cpm_result = cursor.fetchone()
            average_cpm = float(cpm_result[0]) if cpm_result and cpm_result[0] else 0.00
            
            # Try to get from influencer table as fallback
            cursor.execute("""
                SELECT total_earnings, current_balance
                FROM influencers 
                WHERE id = ?
            """, [influencer_id])
            
            influencer_data = cursor.fetchone()
            if influencer_data and influencer_data[0]:
                # If influencer table has data, use it (it might be more up-to-date)
                total_earnings = max(total_earnings, float(influencer_data[0]) if influencer_data[0] else 0.00)
                current_balance = max(current_balance, float(influencer_data[1]) if influencer_data[1] else 0.00)
        else:
            total_earnings = float(financials[0]) if financials[0] else 0.00
            current_balance = float(financials[1]) if financials[1] else 0.00
            pending_earnings = float(financials[2]) if financials[2] else 0.00
            average_cpm = float(financials[3]) if financials[3] else 0.00
        
        # Get content performance data from submitted_posts
        cursor.execute("""
            SELECT 
                sp.id,
                sp.campaign_id,
                c.title as campaign_title,
                sp.post_description as post_title,
                sp.platform,
                sp.post_url,
                sp.views as total_views,
                sp.engagement as total_interactions,
                sp.earnings,
                sp.status,
                sp.submitted_at as posted_date
            FROM submitted_posts sp
            JOIN campaigns c ON sp.campaign_id = c.id
            WHERE sp.influencer_id = ?
            ORDER BY sp.submitted_at DESC
        """, [influencer_id])
        
        content_performance = []
        for row in cursor.fetchall():
            # Map status from submitted_posts to display status
            status_map = {
                'pending_review': 'Under Review',
                'approved': 'Verified',
                'paid': 'Payment Processed',
                'rejected': 'Rejected'
            }
            display_status = status_map.get(row[9], row[9] or 'Pending')
            
            content_performance.append({
                "id": row[0],
                "campaign_id": row[1],
                "campaign_title": row[2],
                "post_title": row[3] or f"Post for {row[2]}",
                "platform": row[4],
                "post_url": row[5],
                "total_views": row[6] or 0,
                "total_interactions": row[7] or 0,
                "earnings": float(row[8]) if row[8] else 0.00,
                "status": display_status,
                "posted_date": row[10]
            })
        
        # Get earnings trend data (last 90 days) - group by week for better visualization
        cursor.execute("""
            SELECT 
                date(submitted_at, 'weekday 0', '-6 days') as week_start,
                COALESCE(SUM(earnings), 0) as weekly_earnings
            FROM submitted_posts
            WHERE influencer_id = ? 
                AND status = 'paid'
                AND submitted_at >= date('now', '-90 days')
            GROUP BY week_start
            ORDER BY week_start
        """, [influencer_id])
        
        earnings_trend = {"dates": [], "amounts": []}
        for row in cursor.fetchall():
            if row[0]:  # Make sure we have a date
                earnings_trend["dates"].append(row[0])
                earnings_trend["amounts"].append(float(row[1]) if row[1] else 0.00)
        
        # If no earnings data, provide empty arrays
        if not earnings_trend["dates"]:
            earnings_trend = {"dates": [], "amounts": []}
        
        # Get payout history
        cursor.execute("""
            SELECT 
                payout_date,
                amount,
                description,
                status
            FROM payout_history
            WHERE influencer_id = ?
            ORDER BY payout_date DESC
            LIMIT 20
        """, [influencer_id])
        
        payout_history = []
        for row in cursor.fetchall():
            payout_history.append({
                "date": row[0],
                "amount": float(row[1]) if row[1] else 0.00,
                "description": row[2],
                "status": row[3]
            })
        
        return jsonify({
            "financial_summary": {
                "total_earnings": total_earnings,
                "current_balance": current_balance,
                "pending_earnings": pending_earnings,
                "average_cpm": average_cpm
            },
            "content_performance": content_performance,
            "earnings_trend": earnings_trend,
            "payout_history": payout_history
        }), 200
        
    except Exception as e:
        app.logger.error(f"Creator Analytics Data Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        
        # Return empty data structure instead of error for better UX
        return jsonify({
            "financial_summary": {
                "total_earnings": 0.00,
                "current_balance": 0.00,
                "pending_earnings": 0.00,
                "average_cpm": 0.00
            },
            "content_performance": [],
            "earnings_trend": {"dates": [], "amounts": []},
            "payout_history": [],
            "error": str(e)
        }), 200
    finally:
        if conn:
            conn.close()

@app.route('/api/creator/request-payout', methods=['POST'])
@login_required(role='creator')
def request_payout():
    """Handles creator payout requests."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        data = request.get_json()
        amount = data.get('amount')
        
        if not amount or float(amount) <= 0:
            return jsonify({"message": "Invalid payout amount"}), 400
        
        conn, cursor = get_db_connection()
        
        # Check if user has sufficient balance
        cursor.execute("SELECT current_balance FROM creator_financials WHERE influencer_id = ?", [influencer_id])
        financials = cursor.fetchone()
        
        if not financials or float(financials[0]) < float(amount):
            return jsonify({"message": "Insufficient balance for payout"}), 400
        
        # Create payout record
        cursor.execute("""
            INSERT INTO payout_history 
            (influencer_id, payout_date, amount, description, status)
            VALUES (?, date('now'), ?, 'Payout request', 'Pending')
        """, [influencer_id, amount])
        
        # Update balance
        new_balance = float(financials[0]) - float(amount)
        cursor.execute("""
            UPDATE creator_financials 
            SET current_balance = ?, last_updated = CURRENT_TIMESTAMP
            WHERE influencer_id = ?
        """, [new_balance, influencer_id])
        
        conn.commit()
        
        return jsonify({"message": "Payout request submitted successfully"}), 201
        
    except Exception as e:
        app.logger.error(f"Payout Request Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()
# Add this endpoint to your app.py file

@app.route('/api/campaigns/<int:campaign_id>/analytics', methods=['GET'])
@login_required(role='manager')
def get_campaign_analytics(campaign_id):
    """Gets comprehensive analytics data for a specific campaign using REAL DB data."""
    conn = None
    try:
        manager_id = session.get('user_id')
        app.logger.info(f"Fetching analytics for campaign {campaign_id} by manager {manager_id}")
        
        conn, cursor = get_db_connection()
        
        # Verify campaign belongs to manager and get basic info
        cursor.execute("""
            SELECT id, title, client_company, total_budget, compensation_model, 
                   cpm_rate, start_date, end_date, created_at, status, description
            FROM campaigns 
            WHERE id = ? AND manager_id = ?
        """, [campaign_id, manager_id])
        
        campaign = cursor.fetchone()
        if not campaign:
            return jsonify({"message": "Campaign not found or unauthorized"}), 404
        
        # Get applications count by status
        cursor.execute("""
            SELECT status, COUNT(*) 
            FROM applications 
            WHERE campaign_id = ?
            GROUP BY status
        """, [campaign_id])
        
        application_stats = {}
        for row in cursor.fetchall():
            application_stats[row[0]] = row[1]
        
        # Get all submitted posts with performance data
        cursor.execute("""
            SELECT 
                sp.id, sp.influencer_id, sp.platform, sp.views, sp.engagement, 
                sp.earnings, sp.status, sp.submitted_at, i.name
            FROM submitted_posts sp
            JOIN influencers i ON sp.influencer_id = i.id
            WHERE sp.campaign_id = ?
            ORDER BY sp.submitted_at DESC
        """, [campaign_id])
        
        posts = cursor.fetchall()
        
        # Calculate comprehensive metrics
        total_budget = float(campaign[3]) if campaign[3] else 0
        
        # Calculate totals from real posts
        total_spent = sum([float(p[5] or 0) for p in posts])
        total_views = sum([int(p[3] or 0) for p in posts])
        total_engagement = sum([int(p[4] or 0) for p in posts])
        
        # Budget metrics
        budget_consumed_percentage = (total_spent / total_budget * 100) if total_budget > 0 else 0
        budget_remaining = max(0, total_budget - total_spent)
        
        # Performance metrics
        ecpm = (total_spent / total_views * 1000) if total_views > 0 else 0
        ecpm_target = float(campaign[5]) if campaign[5] else 25.0
        
        # Engagement rate
        engagement_rate = (total_engagement / total_views * 100) if total_views > 0 else 0
        
        # Creator performance breakdown
        creators = []
        creator_performance = {}
        
        for post in posts:
            influencer_id = post[1]
            if influencer_id not in creator_performance:
                creator_performance[influencer_id] = {
                    'name': post[8],
                    'views': 0,
                    'engagement': 0,
                    'cost': 0,
                    'posts': 0
                }
            
            creator_performance[influencer_id]['views'] += int(post[3] or 0)
            creator_performance[influencer_id]['engagement'] += int(post[4] or 0)
            creator_performance[influencer_id]['cost'] += float(post[5] or 0)
            creator_performance[influencer_id]['posts'] += 1
        
        # Convert to list and calculate additional metrics
        for influencer_id, perf in creator_performance.items():
            c_engagement_rate = (perf['engagement'] / perf['views'] * 100) if perf['views'] > 0 else 0
            c_ecpm = (perf['cost'] / perf['views'] * 1000) if perf['views'] > 0 else 0
            
            # Quality score calculation
            quality_score = min(100, int(
                (min(c_engagement_rate, 10) * 5) +  
                (50 if c_ecpm <= ecpm_target else 30) + 
                (min(perf['posts'] * 5, 20)) 
            ))
            
            creators.append({
                "name": perf['name'],
                "handle": f"@{perf['name'].replace(' ', '').lower()}",
                "views": perf['views'],
                "engagements": perf['engagement'],
                "engagement_rate": round(c_engagement_rate, 2),
                "cost": round(perf['cost'], 2),
                "ecpm": round(c_ecpm, 2),
                "quality_score": quality_score,
                "posts_count": perf['posts']
            })
        
        # Sort creators by performance (best first)
        creators.sort(key=lambda x: x['quality_score'], reverse=True)
        
        # Health score calculation
        health_score = calculate_campaign_health_score(
            budget_consumed_percentage,
            ecpm,
            ecpm_target,
            engagement_rate,
            len(creators),
            len([app for app in application_stats.items() if app[0] in ['approved', 'paid']])
        )
        
        # Time series data (Real Daily Data)
        dates = []
        actual_views = []
        projected_views = [] # We will use cumulative + linear projection
        
        # Query DB for daily views
        cursor.execute("""
            SELECT 
                DATE(submitted_at) as submit_date, 
                SUM(views) as daily_views
            FROM submitted_posts
            WHERE campaign_id = ?
            GROUP BY DATE(submitted_at)
            ORDER BY submit_date ASC
        """, [campaign_id])
        
        ts_rows = cursor.fetchall()
        
        running_total_views = 0
        
        for row in ts_rows:
            date_str = row[0]
            day_views = row[1] or 0
            
            dates.append(date_str)
            running_total_views += day_views
            actual_views.append(running_total_views) # Cumulative line
        
        # Simple Projection (Linear Extrapolation if we have data)
        if len(actual_views) > 1:
            avg_daily = running_total_views / len(actual_views)
            # Project next 7 days
            last_val = running_total_views
            for i in range(len(actual_views)):
                # Just match actuals for the past
                projected_views.append(actual_views[i])
            
            # Add 3 days of future projection
            # Note: For chart.js we might need future dates, but to keep it simple 
            # we just return the 'trend' line matching actuals for now.
            # Strictly real data means avoiding guesses, so let's keep projection equal to actual
            # or just linear trend.
            pass 
        else:
            projected_views = actual_views # Fallback
            
        # Build comprehensive response
        analytics_data = {
            "campaign": {
                "id": campaign[0],
                "title": campaign[1],
                "client_company": campaign[2],
                "start_date": campaign[6],
                "end_date": campaign[7],
                "status": campaign[9],
                "description": campaign[10]
            },
            "health_score": health_score,
            "budget": {
                "total": total_budget,
                "consumed": round(total_spent, 2),
                "remaining": round(budget_remaining, 2),
                "percentage_consumed": round(budget_consumed_percentage, 1)
            },
            "roi_metrics": {
                "ecpm": round(ecpm, 2),
                "ecpm_target": ecpm_target,
                "cpa": round((total_spent / (total_views * 0.001)) if total_views > 0 else 0, 2),
                "cpa_target": 150.0
            },
            "totals": {
                "views": total_views,
                "engagements": total_engagement,
                "posts": len(posts),
                "creators": len(creators)
            },
            "application_stats": application_stats,
            "time_series": {
                "dates": dates,
                "actual_views": actual_views,
                "projected_views": projected_views
            },
            "creators": creators,
            "top_referrals": creators[:5]
        }
        
        return jsonify(analytics_data), 200
        
    except Exception as e:
        app.logger.error(f"Campaign Analytics Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()
            
def calculate_campaign_health_score(budget_percent, ecpm, ecpm_target, engagement_rate, creators_count, active_creators):
    """Calculate comprehensive campaign health score."""
    score = 75  # Base score
    
    # Budget efficiency (±15 points)
    if 20 <= budget_percent <= 80:
        score += 10
    elif budget_percent > 90:
        score -= 15
    elif budget_percent > 80:
        score -= 5
    elif budget_percent < 10:
        score -= 5
    
    # Cost efficiency (±20 points)
    if ecpm <= ecpm_target:
        score += 20
    elif ecpm <= ecpm_target * 1.2:
        score += 10
    elif ecpm > ecpm_target * 1.5:
        score -= 15
    
    # Engagement performance (±15 points)
    if engagement_rate >= 5:
        score += 15
    elif engagement_rate >= 3:
        score += 8
    elif engagement_rate < 1:
        score -= 10
    
    # Creator activation (±10 points)
    if creators_count > 0:
        activation_rate = (active_creators / creators_count * 100)
        if activation_rate >= 70:
            score += 10
        elif activation_rate < 30:
            score -= 10
    
    return max(0, min(100, score))


def calculate_campaign_health(budget_percent, ecpm, ecpm_target, active_creators, total_creators):
    """Calculate campaign health score based on multiple factors."""
    score = 80  # Base score
    
    # Budget efficiency (±20 points)
    if 30 <= budget_percent <= 70:
        score += 10
    elif budget_percent > 90:
        score -= 20
    elif budget_percent > 80:
        score -= 10
    
    # eCPM performance (±15 points)
    if ecpm < ecpm_target:
        score += 15
    elif ecpm > ecpm_target * 1.5:
        score -= 15
    
    # Creator activation rate (±10 points)
    activation_rate = (active_creators / total_creators * 100) if total_creators > 0 else 0
    if activation_rate > 80:
        score += 10
    elif activation_rate < 50:
        score -= 10
    
    return max(0, min(100, score))
  
@app.route('/api/creator/performance-data', methods=['GET'])
@login_required(role='creator')
def get_creator_performance_data():
    """Gets comprehensive performance data for the creator dashboard."""
    conn = None
    try:
        influencer_id = session.get('user_id')
        conn, cursor = get_db_connection()
        
        # Get financial summary
        cursor.execute("""
            SELECT total_earnings, current_balance, pending_earnings, average_cpm
            FROM creator_financials 
            WHERE influencer_id = ?
        """, [influencer_id])
        
        financials = cursor.fetchone()
        if not financials:
            # Initialize financials if they don't exist
            cursor.execute("""
                INSERT INTO creator_financials (influencer_id) VALUES (?)
            """, [influencer_id])
            conn.commit()
            financials = (0.00, 0.00, 0.00, 0.00)
        
        # Get content performance data
        cursor.execute("""
            SELECT 
                pp.id,
                pp.campaign_id,
                c.title as campaign_title,
                pp.post_title,
                pp.platform,
                pp.post_url,
                pp.total_views,
                pp.total_interactions,
                pp.earnings,
                pp.status,
                pp.posted_date
            FROM post_performance pp
            JOIN campaigns c ON pp.campaign_id = c.id
            WHERE pp.influencer_id = ?
            ORDER BY pp.posted_date DESC
        """, [influencer_id])
        
        content_performance = []
        for row in cursor.fetchall():
            content_performance.append({
                "id": row[0],
                "campaign_id": row[1],
                "campaign_title": row[2],
                "post_title": row[3],
                "platform": row[4],
                "post_url": row[5],
                "total_views": row[6],
                "total_interactions": row[7],
                "earnings": float(row[8]) if row[8] else 0.00,
                "status": row[9],
                "posted_date": row[10]
            })
        
        # Get earnings trend data (last 90 days)
        cursor.execute("""
            SELECT 
                date(eh.earnings_date) as date,
                COALESCE(SUM(eh.amount), 0) as daily_earnings
            FROM earnings_history eh
            WHERE eh.influencer_id = ? 
                AND eh.earnings_date >= date('now', '-90 days')
            GROUP BY date(eh.earnings_date)
            ORDER BY date(eh.earnings_date)
        """, [influencer_id])
        
        earnings_trend = {"dates": [], "amounts": []}
        for row in cursor.fetchall():
            earnings_trend["dates"].append(row[0])
            earnings_trend["amounts"].append(float(row[1]) if row[1] else 0.00)
        
        # Get payout history
        cursor.execute("""
            SELECT 
                payout_date,
                amount,
                description,
                status
            FROM payout_history
            WHERE influencer_id = ?
            ORDER BY payout_date DESC
            LIMIT 20
        """, [influencer_id])
        
        payout_history = []
        for row in cursor.fetchall():
            payout_history.append({
                "date": row[0],
                "amount": float(row[1]) if row[1] else 0.00,
                "description": row[2],
                "status": row[3]
            })
        
        return jsonify({
            "financial_summary": {
                "total_earnings": float(financials[0]) if financials[0] else 0.00,
                "current_balance": float(financials[1]) if financials[1] else 0.00,
                "pending_earnings": float(financials[2]) if financials[2] else 0.00,
                "average_cpm": float(financials[3]) if financials[3] else 0.00
            },
            "content_performance": content_performance,
            "earnings_trend": earnings_trend,
            "payout_history": payout_history
        }), 200
        
    except Exception as e:
        app.logger.error(f"Creator Performance Data Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

# Add these routes to your existing app.py file

# --- Super Admin API Routes ---

# Update the influencers API endpoint in app.py

@app.route('/api/admin/influencers', methods=['GET'])
@login_required(role='admin')
def get_all_influencers():
    """Gets all influencers with their application counts and performance data."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # First, let's check what columns exist in the influencers table
        cursor.execute("PRAGMA table_info(influencers)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        app.logger.info(f"Influencers table columns: {column_names}")
        
        # Build query based on available columns
        select_columns = ["i.id", "i.name", "i.email"]
        
        # Add optional columns if they exist
        optional_columns = ['number', 'instagram', 'tik_tok', 'youtube', 'avg_views', 'reach']
        for col in optional_columns:
            if col in column_names:
                select_columns.append(f"i.{col}")
            else:
                select_columns.append(f"NULL as {col}")
        
        # Add status if it exists, otherwise use default
        if 'status' in column_names:
            select_columns.append("i.status")
        else:
            select_columns.append("'pending' as status")
        
        # Build the query
        base_query = f"""
            SELECT 
                {', '.join(select_columns)}
            FROM influencers i
            ORDER BY i.name ASC
        """
        
        app.logger.info(f"Executing query: {base_query}")
        cursor.execute(base_query)
        
        influencers = []
        for row in cursor.fetchall():
            influencer_data = {
                "id": row[0],
                "name": row[1],
                "email": row[2],
                "number": row[3],
                "instagram": row[4],
                "tik_tok": row[5],
                "youtube": row[6],
                "avg_views": row[7],
                "reach": row[8],
                "status": row[9] or 'pending',
                "application_count": 0,
                "total_earnings": 0.00
            }
            
            # Get application count for this influencer
            try:
                cursor.execute("SELECT COUNT(*) FROM applications WHERE influencer_id = ?", [row[0]])
                app_count_result = cursor.fetchone()
                influencer_data["application_count"] = app_count_result[0] if app_count_result else 0
            except Exception as e:
                app.logger.warning(f"Could not get application count for influencer {row[0]}: {e}")
                influencer_data["application_count"] = 0
            
            # Get total earnings for this influencer
            try:
                cursor.execute("SELECT COALESCE(SUM(earnings), 0) FROM post_performance WHERE influencer_id = ?", [row[0]])
                earnings_result = cursor.fetchone()
                influencer_data["total_earnings"] = float(earnings_result[0]) if earnings_result and earnings_result[0] else 0.00
            except Exception as e:
                app.logger.warning(f"Could not get earnings for influencer {row[0]}: {e}")
                influencer_data["total_earnings"] = 0.00
            
            influencers.append(influencer_data)
        
        app.logger.info(f"Returning {len(influencers)} influencers")
        return jsonify(influencers), 200
        
    except Exception as e:
        app.logger.error(f"Get All Influencers Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        
        # Return empty array instead of error for frontend compatibility
        return jsonify([]), 200
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/influencers/<int:influencer_id>', methods=['GET'])
@login_required(role='admin')
def get_influencer_details(influencer_id):
    """Gets detailed information for a specific influencer."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # Check what columns exist
        cursor.execute("PRAGMA table_info(influencers)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        # Build query based on available columns
        select_columns = ["id", "name", "email"]
        
        # Add optional columns if they exist
        optional_columns = ['number', 'instagram', 'tik_tok', 'youtube', 'avg_views', 'reach']
        for col in optional_columns:
            if col in column_names:
                select_columns.append(col)
            else:
                select_columns.append(f"NULL as {col}")
        
        # Add status if it exists
        if 'status' in column_names:
            select_columns.append("status")
        else:
            select_columns.append("'pending' as status")
        
        # Build the query
        query = f"SELECT {', '.join(select_columns)} FROM influencers WHERE id = ?"
        
        cursor.execute(query, [influencer_id])
        influencer_row = cursor.fetchone()
        
        if not influencer_row:
            return jsonify({"message": "Influencer not found"}), 404
        
        # Get application count
        application_count = 0
        try:
            cursor.execute("SELECT COUNT(*) FROM applications WHERE influencer_id = ?", [influencer_id])
            app_count_result = cursor.fetchone()
            application_count = app_count_result[0] if app_count_result else 0
        except Exception as e:
            app.logger.warning(f"Could not get application count: {e}")
        
        # Get total earnings
        total_earnings = 0.00
        try:
            cursor.execute("SELECT COALESCE(SUM(earnings), 0) FROM post_performance WHERE influencer_id = ?", [influencer_id])
            earnings_result = cursor.fetchone()
            total_earnings = float(earnings_result[0]) if earnings_result and earnings_result[0] else 0.00
        except Exception as e:
            app.logger.warning(f"Could not get total earnings: {e}")
        
        influencer = {
            "id": influencer_row[0],
            "name": influencer_row[1],
            "email": influencer_row[2],
            "number": influencer_row[3],
            "instagram": influencer_row[4],
            "tik_tok": influencer_row[5],
            "youtube": influencer_row[6],
            "avg_views": influencer_row[7],
            "reach": influencer_row[8],
            "status": influencer_row[9] or 'pending',
            "application_count": application_count,
            "total_earnings": total_earnings
        }
        
        return jsonify(influencer), 200
        
    except Exception as e:
        app.logger.error(f"Get Influencer Details Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/campaigns', methods=['GET'])
@login_required(role='admin')
def get_all_campaigns_admin():
    """Gets all campaigns for the admin panel."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                id, title, client_company, cpm_rate, total_budget,
                compensation_model, status, created_at
            FROM campaigns 
            ORDER BY created_at DESC
        """)
        
        campaigns = []
        for row in cursor.fetchall():
            campaigns.append({
                "id": row[0],
                "title": row[1],
                "client_company": row[2],
                "cpm_rate": float(row[3]) if row[3] else 0.00,
                "total_budget": float(row[4]) if row[4] else 0.00,
                "compensation_model": row[5],
                "status": row[6],
                "created_at": row[7]
            })
        
        return jsonify(campaigns), 200
        
    except Exception as e:
        app.logger.error(f"Get All Campaigns Admin Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/submitted-posts', methods=['GET'])
@login_required(role='admin')
def get_all_submitted_posts():
    """Gets all submitted posts with influencer and campaign information."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                sp.id,
                sp.campaign_id,
                sp.influencer_id,
                i.name as influencer_name,
                c.title as campaign_title,
                sp.platform,
                sp.post_url,
                sp.post_description,
                sp.views,
                sp.engagement,
                sp.earnings,
                sp.status,
                sp.submitted_at,
                sp.reviewed_at,
                sp.feedback,
                c.cpm_rate
            FROM submitted_posts sp
            JOIN influencers i ON sp.influencer_id = i.id
            JOIN campaigns c ON sp.campaign_id = c.id
            ORDER BY sp.submitted_at DESC
        """)
        
        posts = []
        for row in cursor.fetchall():
            posts.append({
                "id": row[0],
                "campaign_id": row[1],
                "influencer_id": row[2],
                "influencer_name": row[3],
                "campaign_title": row[4],
                "platform": row[5],
                "post_url": row[6],
                "post_description": row[7],
                "views": row[8],
                "engagement": row[9],
                "earnings": float(row[10]) if row[10] else 0.00,
                "status": row[11],
                "submitted_at": row[12],
                "reviewed_at": row[13],
                "feedback": row[14],
                "cpm_rate": float(row[15]) if row[15] else 0.00
            })
        
        return jsonify(posts), 200
        
    except Exception as e:
        app.logger.error(f"Get All Submitted Posts Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/submitted-posts/<int:post_id>', methods=['GET'])
@login_required(role='admin')
def get_submitted_post_details(post_id):
    """Gets detailed information for a specific submitted post."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                sp.id,
                sp.campaign_id,
                sp.influencer_id,
                i.name as influencer_name,
                c.title as campaign_title,
                sp.platform,
                sp.post_url,
                sp.post_description,
                sp.views,
                sp.engagement,
                sp.earnings,
                sp.status,
                sp.submitted_at,
                sp.reviewed_at,
                sp.feedback,
                c.cpm_rate
            FROM submitted_posts sp
            JOIN influencers i ON sp.influencer_id = i.id
            JOIN campaigns c ON sp.campaign_id = c.id
            WHERE sp.id = ?
        """, [post_id])
        
        post_row = cursor.fetchone()
        if not post_row:
            return jsonify({"message": "Post not found"}), 404
        
        post = {
            "id": post_row[0],
            "campaign_id": post_row[1],
            "influencer_id": post_row[2],
            "influencer_name": post_row[3],
            "campaign_title": post_row[4],
            "platform": post_row[5],
            "post_url": post_row[6],
            "post_description": post_row[7],
            "views": post_row[8],
            "engagement": post_row[9],
            "earnings": float(post_row[10]) if post_row[10] else 0.00,
            "status": post_row[11],
            "submitted_at": post_row[12],
            "reviewed_at": post_row[13],
            "feedback": post_row[14],
            "cpm_rate": float(post_row[15]) if post_row[15] else 0.00
        }
        
        return jsonify(post), 200
        
    except Exception as e:
        app.logger.error(f"Get Submitted Post Details Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/submitted-posts/<int:post_id>', methods=['PUT'])
@login_required(role='admin') 
def update_submitted_post(post_id):
    """
    Updates a post status. If status becomes 'paid', it triggers the payout workflow.
    """
    conn = None
    try:
        data = request.get_json()
        status = data.get('status') # e.g., 'paid', 'rejected', 'approved'
        views = data.get('views', 0)
        engagement = data.get('engagement', 0)
        earnings_input = data.get('earnings')
        post_description = data.get('post_description', '')
        
        if not status:
            return jsonify({"message": "Status is required"}), 400
        
        conn, cursor = get_db_connection()
        current_time = datetime.now().isoformat()
        
        # 1. Get existing post info
        cursor.execute("SELECT influencer_id, campaign_id, earnings FROM submitted_posts WHERE id = ?", [post_id])
        post_info = cursor.fetchone()
        if not post_info:
            return jsonify({"message": "Post not found"}), 404
        influencer_id, campaign_id, old_earnings = post_info

        # 2. Logic to handle 'rejected' status (Zero out earnings)
        if status == 'rejected':
            earnings = 0.00
        else:
            # If earnings passed, use it; otherwise calculate based on CPM
            if earnings_input is not None:
                earnings = float(earnings_input)
            else:
                cursor.execute("SELECT cpm_rate FROM campaigns WHERE id = ?", [campaign_id])
                cpm_res = cursor.fetchone()
                cpm_rate = float(cpm_res[0]) if cpm_res and cpm_res[0] else 0.00
                views_int = int(views)
                earnings = round((views_int / 1000) * cpm_rate, 2)

        # 3. Update the Submitted Post Record
        cursor.execute("""
            UPDATE submitted_posts 
            SET views = ?, engagement = ?, earnings = ?, status = ?, post_description = ?, updated_at = ?
            WHERE id = ?
        """, [views, engagement, earnings, status, post_description, current_time, post_id])

        # 4. AUTOMATIC PAYOUT GENERATION
        # If the Admin marks the post as 'paid', we assume the funds are released to the user's wallet
        # AND we generate a Payout Request so the Admin sees it in the 'Payouts' tab for processing.
        if status == 'paid' and earnings > 0:
            description = f"Earnings for Post #{post_id}"
            
            # Check for duplicates to avoid paying twice for the same post ID
            cursor.execute("SELECT id FROM payout_history WHERE description = ? AND influencer_id = ?", [description, influencer_id])
            existing_payout = cursor.fetchone()
            
            if not existing_payout:
                cursor.execute("""
                    INSERT INTO payout_history 
                    (influencer_id, payout_date, amount, description, status, created_at)
                    VALUES (?, date('now'), ?, ?, 'Pending', ?)
                """, [influencer_id, earnings, description, current_time])

        # 5. Recalculate Totals (The "Source of Truth" update)
        # This ensures total_earnings matches strictly what is in the db
        recalculate_financial_summary(cursor, influencer_id)
        
        # 6. Update Campaign Budget Cache (Optional but good for analytics)
        cursor.execute("""
            UPDATE campaigns 
            SET budget_used = (
                SELECT SUM(earnings) FROM submitted_posts WHERE campaign_id = ? AND status = 'paid'
            )
            WHERE id = ?
        """, [campaign_id, campaign_id])

        conn.commit()
        return jsonify({"message": "Post updated, financials synced, and payout request created."}), 200

    except Exception as e:
        app.logger.error(f"Update Post Error: {e}")
        if conn: conn.rollback()
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/payouts', methods=['GET'])
@login_required(role='admin')
def get_all_payouts():
    """Gets all payout history."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                ph.id,
                ph.influencer_id,
                i.name as influencer_name,
                ph.payout_date,
                ph.amount,
                ph.description,
                ph.status,
                ph.reference_number,
                ph.created_at
            FROM payout_history ph
            JOIN influencers i ON ph.influencer_id = i.id
            ORDER BY ph.payout_date DESC
        """)
        
        payouts = []
        for row in cursor.fetchall():
            payouts.append({
                "id": row[0],
                "influencer_id": row[1],
                "influencer_name": row[2],
                "payout_date": row[3],
                "amount": float(row[4]) if row[4] else 0.00,
                "description": row[5],
                "status": row[6],
                "reference_number": row[7],
                "created_at": row[8]
            })
        
        return jsonify(payouts), 200
        
    except Exception as e:
        app.logger.error(f"Get All Payouts Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/export-data', methods=['GET'])
@login_required(role='admin')
def export_admin_data():
    """Exports admin data as CSV."""
    conn = None
    try:
        export_type = request.args.get('type', 'influencers')
        
        conn, cursor = get_db_connection()
        
        if export_type == 'influencers':
            cursor.execute("""
                SELECT 
                    i.name, i.email, i.number, i.instagram, i.tik_tok, i.youtube,
                    i.avg_views, i.reach, i.created_at, i.status,
                    COUNT(DISTINCT a.id) as application_count,
                    COALESCE(SUM(pp.earnings), 0) as total_earnings
                FROM influencers i
                LEFT JOIN applications a ON i.id = a.influencer_id
                LEFT JOIN post_performance pp ON i.id = pp.influencer_id
                GROUP BY i.id
                ORDER BY i.created_at DESC
            """)
            
            data = []
            for row in cursor.fetchall():
                data.append({
                    "Name": row[0],
                    "Email": row[1],
                    "Phone": row[2] or 'N/A',
                    "Instagram": row[3] or 'N/A',
                    "TikTok": row[4] or 'N/A',
                    "YouTube": row[5] or 'N/A',
                    "Average Views": row[6] or 0,
                    "Reach": row[7] or 0,
                    "Joined Date": row[8],
                    "Status": row[9] or 'pending',
                    "Applications": row[10],
                    "Total Earnings": f"R {float(row[11]):.2f}" if row[11] else "R 0.00"
                })
                
        elif export_type == 'posts':
            cursor.execute("""
                SELECT 
                    i.name as influencer_name,
                    c.title as campaign_title,
                    sp.platform,
                    sp.post_url,
                    sp.views,
                    sp.engagement,
                    sp.earnings,
                    sp.status,
                    sp.submitted_at,
                    sp.reviewed_at
                FROM submitted_posts sp
                JOIN influencers i ON sp.influencer_id = i.id
                JOIN campaigns c ON sp.campaign_id = c.id
                ORDER BY sp.submitted_at DESC
            """)
            
            data = []
            for row in cursor.fetchall():
                data.append({
                    "Influencer": row[0],
                    "Campaign": row[1],
                    "Platform": row[2],
                    "Post URL": row[3],
                    "Views": row[4] or 0,
                    "Engagement": row[5] or 0,
                    "Earnings": f"R {float(row[6]):.2f}" if row[6] else "R 0.00",
                    "Status": row[7],
                    "Submitted At": row[8],
                    "Reviewed At": row[9] or 'Not reviewed'
                })
                
        elif export_type == 'payouts':
            cursor.execute("""
                SELECT 
                    i.name as influencer_name,
                    ph.payout_date,
                    ph.amount,
                    ph.description,
                    ph.status,
                    ph.reference_number
                FROM payout_history ph
                JOIN influencers i ON ph.influencer_id = i.id
                ORDER BY ph.payout_date DESC
            """)
            
            data = []
            for row in cursor.fetchall():
                data.append({
                    "Influencer": row[0],
                    "Payout Date": row[1],
                    "Amount": f"R {float(row[2]):.2f}" if row[2] else "R 0.00",
                    "Description": row[3],
                    "Status": row[4],
                    "Reference Number": row[5] or 'N/A'
                })
        
        else:
            return jsonify({"message": "Invalid export type"}), 400
        
        # Convert to CSV
        import csv
        import io
        
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        
        return jsonify({
            "csv_data": output.getvalue(),
            "filename": f"creative_swop_{export_type}_{datetime.now().strftime('%Y%m%d')}.csv"
        }), 200
        
    except Exception as e:
        app.logger.error(f"Export Data Error: {e}")
        return jsonify({"message": "An internal server error occurred"}), 500
    finally:
        if conn:
            conn.close()

# Update the login_required decorator to support admin role
def login_required(role=None):
    """Decorator to require login and optionally check role."""
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({"message": "Authentication required"}), 401
            
            # For admin routes, check if user is admin
            if role == 'admin':
                # You'll need to implement admin user checking
                # This is a placeholder - implement your admin verification logic
                if session.get('role') != 'admin':
                    return jsonify({"message": "Admin access required"}), 403
            
            if role and session.get('role') != role:
                return jsonify({"message": f"Unauthorized - {role} access required"}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- UPDATE THIS ROUTE IN app.py ---

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """Handles admin login using the 'admin' table."""
    conn = None
    try:
        conn, cursor = get_db_connection()
    except Exception as e:
        return jsonify({"message": "Database connection error."}), 500

    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"message": "Missing email or password"}), 400
    
    try:
        # Corrected table name to 'admin' based on your dbscheme.txt
        cursor.execute("SELECT id, password FROM admin WHERE email = ?", [email])
        admin_row = cursor.fetchone()

        if admin_row:
            admin_id = admin_row[0]
            hashed_pass = admin_row[1]

            # Use the existing check_password_hash function
            if check_password_hash(hashed_pass, password):
                session.permanent = True
                session['user_id'] = admin_id
                session['role'] = 'admin'
                session['email'] = email
                
                conn.close()
                return jsonify({"message": "Admin login successful"}), 200
            
        conn.close()
        return jsonify({"message": "Invalid admin credentials"}), 401
    
    except Exception as e:
        app.logger.error(f"Admin Login Error: {e}")
        if conn: conn.close()
        return jsonify({"message": "An internal server error occurred during login."}), 500

@app.route('/api/onboard/creator', methods=['POST'])
def onboard_creator():
    data = request.form
    
    # Extract platforms from multiple checkboxes
    platforms = request.form.getlist('platforms') 
    
    # Data to be inserted into Airtable
    record = {
        "User Type": "Creator",
        "Status": "Submitted",
        "Full Name": data.get('full_name'),
        "Email Address": data.get('email_address'),
        "Primary Platforms": platforms,
        "Performance Link": data.get('performance_link'),
    }

    # *** CORRECTION: Call the new database save method ***
    if db.save_creator_submission(record):
        print(f"Creator Submission Successful: {record}") # Log for testing
        return jsonify({"message": "Creator submission successful. Verification email pending."}), 200
    else:
        # If save fails, return an error
        return jsonify({"error": "Failed to submit data to the database. Please try again."}), 500


# app.py

@app.route('/api/onboard/agency', methods=['POST'])
def onboard_agency():
    data = request.form
    
    # Data to be inserted into Airtable
    record = {
        "User Type": "Agency",
        "Status": "Onboarding Call", # Starting agencies at a higher status
        "Business Name": data.get('business_name'),
        "Email Address": data.get('work_email_address'),
        "Phone Number": data.get('work_phone_number'),
        "Website URL": data.get('website_url'),
        "Affiliate Code": data.get('affiliate_code', ''),
        # --- NEW FIELD: Tracking the source affiliate link ---
        "Link Source Code": data.get('affiliate_link_source', ''),
    }

    # *** CORRECTION: Call the new database save method ***
    if db.save_agency_submission(record):
        print(f"Agency Submission Successful: {record}") # Log for testing
        return jsonify({"message": "Agency submission successful. Consultation email sent."}), 200
    else:
        # If save fails, return an error
        return jsonify({"error": "Failed to submit data to the database. Please try again."}), 500

# app.py

# --- AFFILIATE SUBMISSION ROUTE (Handles JSON) ---
# --- AFFILIATE SUBMISSION ROUTE (Handles JSON) ---
@app.route('/api/submit-affiliate', methods=['POST'])
def submit_affiliate():
    try:
        # Get data sent as JSON from the JavaScript fetch request
        raw_data = request.get_json()
        
        # Debug: Print what we received
        print(f"Received raw data: {raw_data}")
        print(f"Data type: {type(raw_data)}")
        
        # Clean the data - ensure everything is JSON serializable
        form_data = {}
        if isinstance(raw_data, dict):
            for key, value in raw_data.items():
                # Convert Response objects or other non-serializable types to strings
                if hasattr(value, '__dict__'):
                    print(f"Warning: Non-serializable object found for key '{key}': {type(value)}")
                    form_data[key] = str(value)
                else:
                    form_data[key] = value
        else:
            return jsonify({
                'success': False,
                'message': 'Invalid data format received'
            }), 400
        
        print(f"Cleaned form data: {form_data}")
        
        # Validate required fields
        required_fields = ['fullName', 'email']
        for field in required_fields:
            if not form_data.get(field):
                return jsonify({'success': False, 'message': f'Missing required field: {field}'}), 400
                
        # --- FIX: CALL THE RENAMED FUNCTION HERE ---
        affiliate_code = generate_random_affiliate_id()
        
        # Try to save to Airtable
        if db.save_affiliate_submission(form_data, affiliate_code):
            # Return the success message with the generated code
            return jsonify({
                'success': True,
                'affiliate_code': affiliate_code,
                'message': 'Affiliate application submitted successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to save affiliate submission to the database'
            }), 500
            
    except Exception as e:
        print(f"Server Error (submit-affiliate): {str(e)}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'message': 'An unexpected error occurred during application submission'
        }), 500

# --- Creator Database Routes ---

@app.route('/api/creators/search', methods=['GET'])
@login_required(role='manager')
def search_creators():
    """
    Searches creators and returns summary cards.
    Supports multiple selected categories using OR logic.
    """
    conn = None
    try:
        query = request.args.get('q', '').lower()
        location_filter = request.args.get('location', '').lower()
        
        # --- LOGIC UPDATE: Handle multiple categories ---
        # Get list of categories (handles ?category=Food&category=Comedy)
        raw_categories = request.args.getlist('category')
        
        # If getlist is empty, check for comma-separated string (handles ?category=Food,Comedy)
        if not raw_categories:
            cat_param = request.args.get('category', '')
            if cat_param:
                raw_categories = [cat_param]

        # Clean up categories: Split commas, strip whitespace, lowercase
        categories = []
        for item in raw_categories:
            if ',' in item:
                categories.extend([c.strip().lower() for c in item.split(',') if c.strip()])
            else:
                if item.strip():
                    categories.append(item.strip().lower())

        conn, cursor = get_db_connection()
        
        # Base query
        sql = """
            SELECT 
                id, name, instagram, tik_tok, youtube, 
                category, location, best_content_link, profile_score, bio
            FROM influencers 
            WHERE 1=1
        """
        params = []
        
        # Dynamic filtering
        if query:
            sql += " AND (lower(name) LIKE ? OR lower(instagram) LIKE ?)"
            params.extend([f'%{query}%', f'%{query}%'])
            
        if location_filter:
            sql += " AND lower(location) LIKE ?"
            params.append(f'%{location_filter}%')
            
        # --- LOGIC UPDATE: Dynamic OR clause for categories ---
        if categories:
            # Creates: AND (lower(category) LIKE ? OR lower(category) LIKE ?)
            or_conditions = ["lower(category) LIKE ?" for _ in categories]
            sql += f" AND ({' OR '.join(or_conditions)})"
            params.extend([f'%{c}%' for c in categories])
            
        sql += " ORDER BY profile_score DESC LIMIT 50"
        
        cursor.execute(sql, params)
        creators = []
        
        for row in cursor.fetchall():
            creators.append({
                "id": row[0],
                "name": row[1],
                "instagram": row[2],
                "tik_tok": row[3],
                "youtube": row[4],
                "category": row[5] or "General",
                "location": row[6] or "Remote",
                "best_content_link": row[7],
                "profile_score": row[8] or 0,
                "bio": row[9]
            })
            
        return jsonify({"creators": creators}), 200
        
    except Exception as e:
        app.logger.error(f"Creator Search Error: {e}")
        return jsonify({"message": "Server error during search"}), 500
    finally:
        if conn: conn.close()
        
@app.route('/api/creators/<int:influencer_id>/details', methods=['GET'])
@login_required(role='manager')
def get_creator_full_details(influencer_id):
    """
    Fetches detailed stats for the popup modal.
    Optimized to use fewer queries.
    """
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # Optimization: Combine Basic Info, Campaign Count, and View Sum into ONE query
        cursor.execute("""
            SELECT 
                i.id, i.name, i.email, i.number, i.instagram, i.tik_tok, i.youtube, 
                i.avg_views, i.reach, i.profile_score, i.location, i.category, i.bio, i.best_content_link,
                (SELECT COUNT(*) FROM applications a WHERE a.influencer_id = i.id AND a.status IN ('accepted', 'approved', 'completed')) as campaign_count,
                (SELECT COALESCE(SUM(views), 0) FROM submitted_posts sp WHERE sp.influencer_id = i.id) as total_views_generated
            FROM influencers i 
            WHERE i.id = ?
        """, [influencer_id])
        
        info = cursor.fetchone()
        
        if not info:
            return jsonify({"message": "Creator not found"}), 404

        # Query 2: Get recent performance (This is hard to join cleanly, so we keep it separate)
        cursor.execute("""
            SELECT platform, views, engagement, submitted_at
            FROM submitted_posts
            WHERE influencer_id = ?
            ORDER BY submitted_at DESC LIMIT 3
        """, [influencer_id])
        
        recent_posts = [dict(zip(['platform', 'views', 'engagement', 'date'], row)) for row in cursor.fetchall()]

        # Map the results (Indices shifted due to combined query)
        data = {
            "id": info[0],
            "name": info[1],
            "email": info[2],
            "phone": info[3],
            "handles": {
                "instagram": info[4],
                "tiktok": info[5],
                "youtube": info[6]
            },
            "stats": {
                "avg_views": info[7],
                "total_reach": info[8],
                "profile_score": info[9],
                "total_campaigns": info[14],       # From subquery
                "total_views_generated": info[15]  # From subquery
            },
            "details": {
                "location": info[10] or "Not specified",
                "category": info[11] or "General",
                "bio": info[12] or "No bio available.",
                "best_content_link": info[13]
            },
            "recent_posts": recent_posts
        }
        
        return jsonify(data), 200
        
    except Exception as e:
        app.logger.error(f"Creator Details Error: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({"message": "Server error fetching details"}), 500
    finally:
        if conn: conn.close()

# --- ADD THESE ROUTES TO app.py UNDER THE SUPER ADMIN SECTION ---

@app.route('/api/admin/stats', methods=['GET'])
@login_required(role='admin')
def get_admin_global_stats():
    """Fetches high-level platform analytics for the Super Admin Dashboard."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # 1. Total Creators & Marketers
        cursor.execute("SELECT COUNT(*) FROM influencers")
        total_creators = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM marketers")
        total_marketers = cursor.fetchone()[0]
        
        # 2. Financials (Total Payouts & Pending)
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payout_history WHERE status = 'Completed'")
        total_paid_out = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(SUM(earnings), 0) FROM post_performance")
        total_generated_value = cursor.fetchone()[0]
        
        # 3. Campaign Health
        cursor.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'live'")
        active_campaigns = cursor.fetchone()[0]
        
        return jsonify({
            "total_creators": total_creators,
            "total_marketers": total_marketers,
            "total_paid_out": total_paid_out,
            "total_generated_value": total_generated_value,
            "active_campaigns": active_campaigns
        }), 200
    except Exception as e:
        app.logger.error(f"Admin Stats Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/marketers', methods=['GET'])
@login_required(role='admin')
def get_all_marketers_admin():
    """Gets all marketer accounts for the admin panel."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        cursor.execute("""
            SELECT 
                id, company, log_email, ad_number, location, category,
                (SELECT COUNT(*) FROM campaigns c WHERE c.manager_id = marketers.id) as campaign_count
            FROM marketers
            ORDER BY id DESC
        """)
        
        marketers = []
        for row in cursor.fetchall():
            marketers.append({
                "id": row[0],
                "company": row[1],
                "email": row[2],
                "phone": row[3],
                "location": row[4],
                "category": row[5],
                "campaign_count": row[6]
            })
            
        return jsonify(marketers), 200
    except Exception as e:
        app.logger.error(f"Get Marketers Admin Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

# 1. Update Marketer (Agency) Details
@app.route('/api/admin/marketers/<int:id>', methods=['PUT'])
@login_required(role='admin') # Keeping 'manager' role for admin access as per your setup
def update_marketer_admin(id):
    """Admin update for marketer account details."""
    conn = None
    try:
        data = request.get_json()
        conn, cursor = get_db_connection()
        
        # Allowed fields to update
        cursor.execute("""
            UPDATE marketers 
            SET company = ?, log_email = ?, ad_number = ?, location = ?, category = ?, brand_description = ?
            WHERE id = ?
        """, [
            data.get('company'), data.get('email'), data.get('phone'), 
            data.get('location'), data.get('category'), data.get('brand_description'), id
        ])
        
        conn.commit()
        return jsonify({"message": "Agency updated successfully"}), 200
    except Exception as e:
        app.logger.error(f"Admin Update Marketer Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

# 2. Update Creator (Influencer) Details
# Replace the existing 'update_influencer_admin' function in app.py

@app.route('/api/admin/influencers/<int:id>', methods=['PUT'])
@login_required(role='admin')
def update_influencer_admin(id):
    """Admin update for influencer account details including profile, niche, and password."""
    conn = None
    try:
        data = request.get_json()
        conn, cursor = get_db_connection()
        
        # 1. Prepare Password Update (Only if provided)
        new_password = data.get('password')
        password_sql = ""
        params = [
            data.get('name'), 
            data.get('email'), 
            data.get('number'), 
            data.get('instagram'), 
            data.get('tik_tok'), 
            data.get('youtube'),
            # NEW FIELDS ADDED HERE:
            data.get('location'),
            data.get('category'),
            data.get('bio'),
            data.get('best_content_link'),
            data.get('profile_score')
        ]

        # Only hash and update password if the admin typed one in
        if new_password and str(new_password).strip():
            hashed_pw = hash_password(new_password) 
            password_sql = ", password = ?"
            params.append(hashed_pw)
        
        # Add ID for the WHERE clause
        params.append(id)

        # 2. Execute SQL Update with new columns
        query = f"""
            UPDATE influencers 
            SET name = ?, email = ?, number = ?, instagram = ?, 
                tik_tok = ?, youtube = ?, location = ?, category = ?, 
                bio = ?, best_content_link = ?, profile_score = ?{password_sql}
            WHERE id = ?
        """
        
        cursor.execute(query, params)
        conn.commit()
        
        return jsonify({"message": "Creator updated successfully"}), 200
        
    except Exception as e:
        app.logger.error(f"Admin Update Influencer Error: {e}")
        if conn: conn.rollback()
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()
                
# 3. Payout Management (Modify Amount & Status)
@app.route('/api/admin/payouts/<int:id>', methods=['PUT'])
@login_required(role='admin')
def update_payout_admin(id):
    """Admin update for payout requests."""
    conn = None
    try:
        data = request.get_json()
        new_status = data.get('status')
        new_amount = data.get('amount')
        
        conn, cursor = get_db_connection()
        
        # Update the payout record
        cursor.execute("""
            UPDATE payout_history 
            SET status = ?, amount = ?
            WHERE id = ?
        """, [new_status, new_amount, id])
        
        # If marked as Completed, ensure it reflects in financials
        if new_status == 'Completed':
            # Get influencer ID to update their balance if needed
            cursor.execute("SELECT influencer_id FROM payout_history WHERE id = ?", [id])
            row = cursor.fetchone()
            if row:
                recalculate_financial_summary(cursor, row[0])
        
        conn.commit()
        return jsonify({"message": "Payout updated successfully"}), 200
    except Exception as e:
        app.logger.error(f"Admin Update Payout Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

# 4. Get Creators for a Specific Campaign (For Management)
@app.route('/api/admin/campaigns/<int:campaign_id>/creators', methods=['GET'])
@login_required(role='admin')
def get_campaign_creators_admin(campaign_id):
    """Fetch all creators part of a specific campaign."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        cursor.execute("""
            SELECT 
                a.id as application_id, 
                i.id as influencer_id, 
                i.name, 
                i.email, 
                a.status 
            FROM applications a
            JOIN influencers i ON a.influencer_id = i.id
            WHERE a.campaign_id = ?
        """, [campaign_id])
        
        creators = []
        for row in cursor.fetchall():
            creators.append({
                "application_id": row[0],
                "influencer_id": row[1],
                "name": row[2],
                "email": row[3],
                "status": row[4]
            })
        return jsonify(creators), 200
    except Exception as e:
        app.logger.error(f"Get Campaign Creators Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

# 5. Remove Creator from Campaign
@app.route('/api/admin/campaigns/<int:campaign_id>/remove-creator', methods=['POST'])
@login_required(role='admin')
def remove_creator_from_campaign(campaign_id):
    """Removes a creator from a campaign (Deletes application)."""
    conn = None
    try:
        data = request.get_json()
        influencer_id = data.get('influencer_id')
        
        conn, cursor = get_db_connection()
        
        # Delete application
        cursor.execute("DELETE FROM applications WHERE campaign_id = ? AND influencer_id = ?", [campaign_id, influencer_id])
        
        # Optional: Clean up submitted posts or mark them as rejected?
        # For now, we'll just leave posts for record keeping or you can delete them:
        # cursor.execute("DELETE FROM submitted_posts WHERE campaign_id = ? AND influencer_id = ?", [campaign_id, influencer_id])
        
        conn.commit()
        return jsonify({"message": "Creator removed from campaign"}), 200
    except Exception as e:
        app.logger.error(f"Remove Creator Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

# 1. Get All Affiliate Campaigns (Global View)
@app.route('/api/admin/affiliate-campaigns', methods=['GET'])
@login_required(role='admin') # Admin uses manager role login in your setup
def get_all_affiliate_campaigns_admin():
    """Fetches all affiliate campaigns for the super admin."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # Join with Marketers to see who owns the campaign
        # Join with Affiliate Code to count active influencers
        cursor.execute("""
            SELECT 
                ac.id, 
                ac.product_name, 
                ac.brand, 
                m.company, 
                ac.status,
                ac.flat_fee,
                COUNT(af.id) as creator_count
            FROM affiliate_campaigns ac
            JOIN marketers m ON ac.manager_id = m.id
            LEFT JOIN affiliate_code af ON ac.id = af.campaign_id
            GROUP BY ac.id
            ORDER BY ac.created_at DESC
        """)
        
        campaigns = []
        for row in cursor.fetchall():
            campaigns.append({
                "id": row[0],
                "product_name": row[1],
                "brand": row[2],
                "agency_name": row[3],
                "status": row[4],
                "commission": row[5],
                "creator_count": row[6]
            })
            
        return jsonify(campaigns), 200
    except Exception as e:
        app.logger.error(f"Admin Affiliate Campaigns Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

# 2. Get Creators for a specific Affiliate Campaign
@app.route('/api/admin/affiliate-campaigns/<int:campaign_id>/codes', methods=['GET'])
@login_required(role='admin')
def get_affiliate_campaign_creators_admin(campaign_id):
    """Fetches all creators/codes for a specific affiliate campaign."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        cursor.execute("""
            SELECT 
                ac.id, 
                ac.affiliate_code, 
                ac.use_count, 
                i.name, 
                i.email
            FROM affiliate_code ac
            JOIN influencers i ON ac.influencer_id = i.id
            WHERE ac.campaign_id = ?
        """, [campaign_id])
        
        codes = []
        for row in cursor.fetchall():
            codes.append({
                "code_id": row[0],
                "code": row[1],
                "uses": row[2],
                "creator_name": row[3],
                "creator_email": row[4]
            })
            
        return jsonify(codes), 200
    except Exception as e:
        app.logger.error(f"Admin Affiliate Details Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()

# 3. Deactivate (Delete) an Affiliate Code
@app.route('/api/admin/affiliate-codes/<int:code_id>', methods=['DELETE'])
@login_required(role='admin')
def deactivate_affiliate_code_admin(code_id):
    """Admin force-delete of an affiliate code."""
    conn = None
    try:
        conn, cursor = get_db_connection()
        
        # Delete the code
        cursor.execute("DELETE FROM affiliate_code WHERE id = ?", [code_id])
        
        # Optional: Delete associated clicks to keep DB clean?
        # cursor.execute("DELETE FROM affiliate_clicks WHERE affiliate_code_id = ?", [code_id])
        
        conn.commit()
        return jsonify({"message": "Affiliate code deactivated successfully"}), 200
    except Exception as e:
        app.logger.error(f"Admin Deactivate Code Error: {e}")
        return jsonify({"message": "Server error"}), 500
    finally:
        if conn: conn.close()
        
# Add admin route to your frontend routes
@app.route('/admin')
def admin_page():
    """Serves the super admin panel."""
    return render_template('super_admin.html')  # You'll need to create this file


# --- Frontend Routes (Serving HTML Files) ---

@app.route('/')
def index():
    """Default route redirects to the login page."""
    return render_template('index.html')

# --- Frontend Routes for recycling (Serving HTML Files) ---

@app.route('/how-it-works')
def how_it_works():
    """Default route redirects to the login page."""
    return render_template('how_it_works.html')

@app.route('/contact-information')
def contactus():
    """Default route redirects to the login page."""
    return render_template('contact_info.html')

@app.route('/schools')
def schools():
    """Default route redirects to the login page."""
    return render_template('school_program.html')

@app.route('/login')
def login_page():
    """Serves the main login page."""
    return render_template('login.html')

@app.route('/register')
def register_page():
    """Serves the user registration page."""
    return render_template('register.html')

# --- Protected Routes ---

@app.route('/analytics')
def my_analytics():
    return render_template('my_analytics.html')

@app.route('/creator')
def creator_page():
    return render_template('creator.html')

@app.route('/media_dashboard')
def media_dashboard_page():
    return render_template('media_dashboard.html')

@app.route('/all_campaigns')
def all_campaigns_page():
    return render_template('all_campaigns.html')

@app.route('/my_campaign')
def my_campaign_page():
    return render_template('my_campaign.html')

@app.route('/campaign_analytics')
@login_required(role='manager')
def campaign_analytics_page():
    return render_template('campaign_analytics.html')

@app.route('/settings')
def settings_page():
    """Serves the profile settings page."""
    return render_template('settings.html')

@app.route('/affiliate-analytics')
@login_required(role='manager')
def affiliate_analytics_page():
    """Serves the affiliate analytics page."""
    return render_template('affiliate_analytics.html')

@app.route('/affiliate-campaign-details')
@login_required(role='manager')
def affiliate_campaign_details_page():
    """Serves the affiliate campaign details page."""
    return render_template('affiliate_campaign_details.html')

@app.route('/join-swop')
def joinswop():
    return render_template('onboarding_gateway.html')

@app.route('/creative-swop/affiliate')
def affiliate():
    return render_template('affiliate.html')

@app.route('/creative-swop/home')
def creativehome():
    return render_template('home.html')

@app.route('/creative-swop/influencers')
def influencers():
    return render_template('influencers.html')

@app.route('/form')
def form():
    return render_template('form.html')  

@app.route('/creator-database')
@login_required(role='manager')
def creator_database_page():
    """Serves the Creator Database Hub page."""
    return render_template('creator_database.html')
 
@app.route('/terms-and-conditions')
def terms_and_conditions():
    """Serves the Creator Database Hub page."""
    return render_template('tnc.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

