from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import cv2
import easyocr
import json
import re
from PIL import Image, ImageDraw
from selenium.common.exceptions import NoSuchElementException
import time
import threading
import asyncio
from selenium.webdriver.common.by import By

app = FastAPI()

# EasyOCR reader 객체 생성 (한글 및 영어 지원)
reader = easyocr.Reader(['ko', 'en'], gpu=True)

# 크롬 드라이버를 글로벌하게 초기화
driver = None

# 크롬 드라이버 설정
def get_chrome_driver():
    global driver
    if driver is None:
        options = Options()
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-notifications")
        options.add_argument("--start-maximized")  # 창을 최대로 시작하지 않도록 설정
        options.add_argument("--disable-images")  # 이미지 로딩 비활성화
        
        # 크롬 드라이버 경로 설정 (자동으로 설치된 드라이버 사용)
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(10)
        driver.set_script_timeout(10)

    return driver

# 비즈니스 번호, 상호명, 거래일시, 금액 추출하는 함수
def process_image(img):
    image = cv2.imread(img)
    if image is None:
        raise Exception(f"이미지를 읽을 수 없습니다: {img}")

    results = reader.readtext(image)
    extracted_text = []

    img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img)

    for detection in results:
        bbox = detection[0]
        text = detection[1]
        extracted_text.append(text)

    def extract_business_numbers(text_list):
        business_number_pattern = re.compile(r'\b\d{3}-?\d{2}-?\d{5}\b')
        return [match for text in text_list for match in business_number_pattern.findall(text)]

    def extract_store_names(text_list):
        store_name_pattern = re.compile(r'(매장명|상호명|회사명|업체명|가맣점명|[상싱성][호오]|[회훼]사)\s*[:;：]?\s*([^\s)]+(?:\s*\S*)*?[점]\s*?\S*)')
        return [match[1] for text in text_list for match in store_name_pattern.findall(text)]

    def is_valid_date(date_str):
        parts = re.split(r'[-/.]', date_str)
        if len(parts) == 3:
            if len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2:
                year, month, day = map(int, parts)
                return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31
            elif len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 4:
                day, month, year = map(int, parts)
                return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31
            elif len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 2:
                month, day, year = map(int, parts)
                year += 2000 if year < 100 else 0
                return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31
        return False
        
    def extract_transaction_date(json_data):
        date_pattern = re.compile(r'([거기][래레][일닐]|[결겔]제[일닐]|거[래레]일시|[결겔]제날짜|날짜|일자)?\s*[:;：]?\s*(\d{4}[-/.]\d{2}[-/.]\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4}|\d{2}[-/.]\d{2}[-/.]\d{2})')
    
        extracted_dates = []
        for text in json_data:
            matches = date_pattern.findall(text)
            for match in matches:
                date_part = match[1]
                if is_valid_date(date_part):
                    extracted_dates.append(date_part)
        return extracted_dates

    def extract_max_number(text_list):
        all_numbers = []
        for text in text_list:
            numbers = re.findall(r'\d{1,3}(?:,\d{3})*', text)
            numbers = [int(num.replace(',', '')) for num in numbers]
            all_numbers.extend(numbers)
        return max(all_numbers, default=0) if all_numbers else None

    info_dict = {
        "사업자번호": extract_business_numbers(extracted_text),
        "가맹점명": extract_store_names(extracted_text),
        "거래일시": extract_transaction_date(extracted_text),
        "금액": extract_max_number(extracted_text)
    }

    return info_dict

# 비즈니스 번호를 통한 카테고리 키워드 추출
async def extract_category_keywords(business_numbers):
    # 크롬 드라이버 작업을 비동기적으로 처리하기 위해서 threading을 사용합니다.
    def run_selenium_task(business_number):
        driver = get_chrome_driver()
        category_keywords_dict = {}

        for business_number in business_numbers:
            business_number_clean = business_number.replace("-", "")
            address = 'https://bizno.net/article/' + business_number_clean

            driver.get(address)

            try:
                shop_name = driver.find_element(By.XPATH, '/html/body/section[2]/div/div/div[1]/div[1]/div/div[1]/div/a/h1').text
                try:
                    category_keywords = driver.find_element(By.XPATH, '/html/body/section[2]/div/div/div[1]/div[1]/div/table/tbody/tr[2]/td').text
                except NoSuchElementException:
                    category_keywords = driver.find_element(By.XPATH, '/html/body/section[2]/div/div/div[1]/div[1]/div/table/tbody/tr[4]/td').text

                category_dict = {
                    "상호명": shop_name,
                    "대분류": re.search(r"대분류\s*:\s*(.*?)(?=\s*중분류|$)", category_keywords),
                    "중분류": re.search(r"중분류\s*:\s*(.*?)(?=\s*소분류|$)", category_keywords),
                    "소분류": re.search(r"소분류\s*:\s*(.*?)(?=\s*세분류|$)", category_keywords),
                    "세분류": re.search(r"세분류\s*:\s*(.*?)(?=\s*세세분류|$)", category_keywords),
                    "세세분류": re.search(r"세세분류\s*:\s*(.*)", category_keywords)
                }

                for key in category_dict:
                    if isinstance(category_dict[key], re.Match):
                        category_dict[key] = category_dict[key].group(1).strip()
                    elif category_dict[key] is not None:
                        category_dict[key] = category_dict[key].strip()

                category_keywords_dict[business_number] = category_dict
            except Exception as e:
                category_keywords_dict[business_number] = None

        return category_keywords_dict

    # 비동기 작업 실행
    return await asyncio.to_thread(run_selenium_task, business_numbers)

@app.on_event("startup")
async def startup():
    # 서버 시작 시 크롬 드라이버를 초기화
    get_chrome_driver()

@app.on_event("shutdown")
async def shutdown():
    # 서버 종료 시 크롬 드라이버 종료
    global driver
    if driver is not None:
        driver.quit()

@app.post("/extract")
async def extract_data(file: UploadFile = File(...)):
    image_path = f"temp_{file.filename}"
    with open(image_path, "wb") as f:
        f.write(await file.read())

    info_dict = process_image(image_path)
    business_numbers = info_dict.get("사업자번호", [])
    
    # 사업자 번호로 카테고리 키워드 추출 (비동기식 처리)
    category_keywords = await extract_category_keywords(business_numbers)

    # 최종 결과 반환
    result = {
        "OCR_결과": info_dict,
        "카테고리_키워드": category_keywords
    }

    return JSONResponse(content=result)