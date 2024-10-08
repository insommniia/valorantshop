import concurrent.futures
from concurrent.futures import as_completed
import requests
from flask import Flask, request, render_template, Response, stream_with_context, jsonify
import json
import os
from bs4 import BeautifulSoup
from urllib.parse import unquote
import re

import asyncio
import sys
import riot_auth

app = Flask(__name__)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def async_auth(username, password):
    auth = riot_auth.RiotAuth()
    await auth.authorize(username, password)
    return auth

def translate_text(text, language):
    if language == "ko-KR":
        return text
    url = 'https://playentry.org/api/expansionBlock/papago/translate/n2mt'
    params = {'text': text, 'target': language.split('-')[0], 'source': 'auto'}
    response = requests.get(url, params=params).json()
    return response.get('translatedText', text)

def get_skin_uuid_by_offerid(offerid, language="ko-KR"):
    base_url = "https://valorant-api.com/v1/weapons/skins"
    response = requests.get(f"{base_url}?language={language}")
    
    if response.status_code != 200:
        return None
    
    skins_data = response.json()["data"]
    
    for skin in skins_data:
        for level in skin.get("levels", []):
            if level["uuid"] == offerid:
                return skin["uuid"]
    
    return None

def get_content_tier_uuid(skin_uuid):
    api_url = f"https://valorant-api.com/v1/weapons/skins/{skin_uuid}"
    
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        skin_data = response.json()
        
        if 'data' in skin_data and 'contentTierUuid' in skin_data['data']:
            return skin_data['data']['contentTierUuid']
        else:
            return None
    
    except requests.exceptions.RequestException:
        return None

def get_content_tier_display_icon(content_tier_uuid):
    api_url = f"https://valorant-api.com/v1/contenttiers/{content_tier_uuid}"
    
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        content_tier_data = response.json()
        
        if 'data' in content_tier_data and 'displayIcon' in content_tier_data['data']:
            return content_tier_data['data']['displayIcon']
        else:
            return None
    
    except requests.exceptions.RequestException:
        return None
@app.route('/')
def index():
    return render_template('index.html')
    
@app.route('/store/<username>/<password>/<region>/<language>/')
def store(username, password, region, language):
    username = unquote(username)
    password = unquote(password)
    region = unquote(region)
    language = unquote(language)

    try:
        auth = asyncio.run(async_auth(username, password))
        
        auth_data = {
            "access_token": auth.access_token,
            "entitlements_token": auth.entitlements_token,
            "puuid": auth.user_id
        }

    except riot_auth.RiotAuthError as e:
        if "mfa" in str(e).lower():
            return render_template('error.html', message=translate_text('2차 인증을 비활성화 해주세요.', language), code=401), 401
        else:
            return render_template('error.html', message=translate_text('서버에서 오류가 발생했습니다.\n입력한 정보가 확실한지 확인하세요\n\n아니면 관리자에게 문의해주세요. https://github.com/stark7k', language), code=500), 500

    except Exception as e:
        error_str = str(e)
        
        code_match = re.search(r'^(\d+)', error_str)
        error_code = int(code_match.group(1)) if code_match else 500

        message_match = re.search(r"message='([^']*)'", error_str)
        error_message = message_match.group(1) if message_match else "Unknown error"

        if language == 'ko-KR':
            translated_error = translate_text(error_message, 'ko-KRR')
        else:
            translated_error = translate_text(error_message, language)
        
        des = translate_text('관리자에게 문의해주세요. https://github.com/stark7k', language)

        plus = translate_text('혹시 403오류신가요?', language)
        pluss = translate_text('라이엇게임즈는 우리가 하는 동작을 좋아하지 않습니다.', language)
        lap = translate_text('그렇기 때문에 그들은 조치를 취해놓았을지도 모릅니다.', language)

        return render_template('error.html', 
            message=translated_error, 
            code=error_code,
            des=des,
            plus=plus,
            pluss=pluss,
            lap=lap), error_code
        
    # auth_data = json.load(open(os.path.join('auth_data.json'), 'r', encoding='utf-8'))

    def generate():
        try:
            client_version = requests.get("https://valorant-api.com/v1/version").json()['data']['riotClientVersion']
        except Exception as e:
            yield b"error"
            return

        headers = {
            "X-Riot-Entitlements-JWT": auth_data['entitlements_token'],
            "Authorization": f"Bearer {auth_data['access_token']}",
            "X-Riot-ClientPlatform": "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjogIldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9",
            "X-Riot-ClientVersion": client_version,
        }

        try:
            store_response = requests.get(f'https://pd.{region}.a.pvp.net/store/v2/storefront/{auth_data["puuid"]}', headers=headers)
            store_response.raise_for_status()
            store = store_response.json()

            wallet_response = requests.get(f'https://pd.{region}.a.pvp.net/store/v1/wallet/{auth_data["puuid"]}', headers=headers)
            wallet_response.raise_for_status()
            wallet = wallet_response.json()

            skin_offers = store['SkinsPanelLayout']['SingleItemStoreOffers']

            def process_skin_offer(offer):
                offer_id = offer['OfferID']
                skin_data = requests.get(f"https://valorant-api.com/v1/weapons/skinlevels/{offer_id}?language={language}").json()["data"]
                
                display_icon = f"https://media.valorant-api.com/weaponskinlevels/{offer_id}/displayicon.png"
                item = skin_data["displayName"]
                eitem = f'/info/{skin_data["uuid"]}/{language}'
                price = str(offer["Cost"]["85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"])

                skin_uuid = get_skin_uuid_by_offerid(offer_id, language)
                tier_icon = ""
                if skin_uuid:
                    content_tier_uuid = get_content_tier_uuid(skin_uuid)
                    if content_tier_uuid:
                        tier_icon = get_content_tier_display_icon(content_tier_uuid) or ""

                return {
                    "display_icon": display_icon,
                    "item": item,
                    "eitem": eitem,
                    "price": price,
                    "tier_icon": tier_icon
                }

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                future_to_offer = {executor.submit(process_skin_offer, offer): i for i, offer in enumerate(skin_offers)}
                results = [None] * len(skin_offers)
                for future in as_completed(future_to_offer):
                    index = future_to_offer[future]
                    results[index] = future.result()

            display_icons = [result["display_icon"] for result in results]
            items = [result["item"] for result in results]
            eitems = [result["eitem"] for result in results]
            prices = [result["price"] for result in results]
            tier_icons = [result["tier_icon"] for result in results]

            bundle_id = store['FeaturedBundle']['Bundles'][0]['DataAssetID']
            bundle_img = f"https://media.valorant-api.com/bundles/{bundle_id}/displayicon.png"
            bundle_name = requests.get(f"https://valorant-api.com/v1/bundles/{bundle_id}?language={language}").json()["data"]["displayName"]
            bundle_price = str(store["FeaturedBundle"]["Bundles"][0]["TotalDiscountedCost"]["85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"])
            enbundle = requests.get(f"https://valorant-api.com/v1/bundles/{bundle_id}").json()["data"]["displayName"]
            yield render_template('store.html',
                val_credits=wallet["Balances"]["85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"],
                rad_points=wallet["Balances"]["e59aa87c-4cbf-517a-5983-6e81511be9b7"],
                kingdom_credits=wallet["Balances"]["85ca954a-41f2-ce94-9b45-8ca3dd39a00d"],
                bundleImg=bundle_img,
                bundle0=bundle_name,
                bundlen=enbundle.lower(),
                bundlePrice0=bundle_price,
                dailyOffer0=display_icons[0],
                dailyOffer1=display_icons[1],
                dailyOffer2=display_icons[2],
                dailyOffer3=display_icons[3],
                item0=items[0],
                item1=items[1],
                item2=items[2],
                item3=items[3],
                eitem0=eitems[0],
                eitem1=eitems[1],
                eitem2=eitems[2],
                eitem3=eitems[3],
                price0=prices[0],
                price1=prices[1],
                price2=prices[2],
                price3=prices[3],
                tier_icon0=tier_icons[0],
                tier_icon1=tier_icons[1],
                tier_icon2=tier_icons[2],
                tier_icon3=tier_icons[3],
                infoinfo=translate_text('자세히 보려면 클릭하세요.', language),
                bundletext=translate_text('번들', language),
                shoptext=translate_text('스킨 상점', language),
            ).encode('utf-8')

        except requests.exceptions.RequestException as e:
            yield translate_text("오류가 발생했습니다.", language).encode('utf-8') + b"<br>"

    return Response(stream_with_context(generate()), content_type='text/html; charset=utf-8')

@app.route('/info/<uuid>/<language>/')
def info(uuid, language):
    base_url = "https://valorant-api.com/v1"
    skin_uuid = get_skin_uuid_by_offerid(uuid, language)
    
    if not skin_uuid:
        return render_template('error.html', message=translate_text("해당 UUID가 없습니다.", language))
    skin_level_url = f"{base_url}/weapons/skinlevels/{uuid}?language={language}"
    skin_level_response = requests.get(skin_level_url)
    
    if skin_level_response.status_code != 200:
        return render_template('error.html', message=translate_text("스킨 레벨 정보를 가져오는 데 실패했습니다.", language))
    
    skin_level_data = skin_level_response.json()["data"]
    skin_level_uuid = skin_level_data["uuid"]
    all_skins_url = f"{base_url}/weapons/skins?language={language}"
    all_skins_response = requests.get(all_skins_url)
    
    if all_skins_response.status_code != 200:
        return render_template('error.html', message=translate_text("스킨 정보를 가져오는 데 실패했습니다.", language))
    
    all_skins_data = all_skins_response.json()["data"]
    target_skin = next((skin for skin in all_skins_data if skin["uuid"] == skin_uuid), None)
    
    if not target_skin:
        return render_template('error.html', message=translate_text("해당 UUID에 해당하는 스킨이 없습니다.", language))
    
    localized_names = {
        "main": target_skin["displayName"],
        "chromas": [chroma["displayName"] for chroma in target_skin.get("chromas", [])],
        "levels": [level["displayName"] for level in target_skin.get("levels", [])]
    }
    
    url = f"https://valorantinfo.kr/skin_details/{uuid}"
    response = requests.get(url)
    
    if response.status_code != 200:
        return render_template('error.html', message=translate_text("스크래핑 실패", language))
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    price_elem = soup.find('p', string=lambda text: 'Price:' in text if text else False)
    price = price_elem.text.split(': ')[1] if price_elem else "Unknown"
    
    def extract_info(container):
        items = []
        if container:
            cards = container.find_all('div', class_='card')
            for card in cards:
                name = card.find('p').text.strip() if card.find('p') else "Unknown"
                image = card.find('img')['src'] if card.find('img') else None
                link = card.find('a')
                video = link['href'].split('url=')[1].split('&')[0] if link and 'href' in link.attrs else None
                items.append({
                    "name": name,
                    "image": image,
                    "video": video
                })
        return items
    
    chroma_container = soup.find('div', class_='skins-container')
    chromas = extract_info(chroma_container)
    
    level_container = soup.find_all('div', class_='skins-container')
    levels = extract_info(level_container[1]) if len(level_container) > 1 else []
    
    for i, chroma in enumerate(chromas):
        chroma['localized_name'] = localized_names['chromas'][i] if i < len(localized_names['chromas']) else chroma['name']
    
    for i, level in enumerate(levels):
        level['localized_name'] = localized_names['levels'][i] if i < len(localized_names['levels']) else level['name']

    offer_id = uuid
    requests.get(f"https://valorant-api.com/v1/weapons/skinlevels/{offer_id}?language={language}").json()["data"]

    skin_uuid = get_skin_uuid_by_offerid(offer_id, language)
    if skin_uuid:
        content_tier_uuid = get_content_tier_uuid(skin_uuid)
        if content_tier_uuid:
            tier_icon = get_content_tier_display_icon(content_tier_uuid)
        else:
                tier_icon = ""
    else:
        tier_icon = ""
    
    return render_template('info.html', 
        skin_name=localized_names['main'],
        uuid=uuid,
        price=price,
        chromas=chromas,
        levels=levels,
        language=language,
        chromainfoinfo = translate_text('크로마', language),
        levelinfoinfo = translate_text('레벨', language),
        infoinfo = translate_text('비디오를 보려면 클릭하세요.', language),
        info = translate_text('정보', language),
        tier_icon = tier_icon
    )


@app.route('/video')
def video():
    video_url = request.args.get('url')
    if not video_url:
        return "Video URL is missing", 400

    decoded_url = unquote(video_url)
    
    return render_template('video.html',
        title = 'Valorant Shop',
        vib = "X",
        videourl=decoded_url
    )

@app.errorhandler(404)
def page_not_found(e):
    language = request.path.split('/')[-1]
    try:
        pagedes = translate_text('페이지를 찾을수 없습니다.', language)
        gohome = translate_text('홈화면으로 가기.', language)
        print(f"Translated texts: pagedes='{pagedes}', gohome='{gohome}'")
    except Exception as trans_error:
        pagedes = '페이지를 찾을수 없습니다.'
        gohome = '홈화면으로 가기.'
    
    return render_template('404.html', pagedes=pagedes, gohome=gohome), 404

@app.errorhandler(500)
def internal_server_error(e):
    language = request.args.get('language', 'en')
    return render_template('error.html', message=translate_text('Error: https://github.com/MonkeySp1n', language), code=500), 500
