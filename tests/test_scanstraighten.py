"""Independent drawn rulings verify local correction, rather than regenerating the warp."""
from PIL import Image, ImageChops, ImageDraw, ImageFont
from math import pi, sin
from pathlib import Path
import pytest

from app.deskew import estimate_angle
from app.output import OperationCancelled
from app.scanstraighten import estimate_bands, straighten_bands, straighten_photo


def photographed_lines(direction=1, region='bottom'):
    image=Image.new('RGB',(800,1200),'white')
    for index,row in enumerate(range(90,1180,75)):
        position=row/image.height
        if region=='bottom':
            angle=0 if row<600 else direction*(row-600)/580*3.2
        else:
            start,stop=(0.,.5) if region=='top' else (.25,.75)
            angle=direction*2.6*sin(pi*(position-start)/(stop-start)) if start<position<stop else 0
        line=Image.new('RGBA',(800,140),(0,0,0,0))
        draw=ImageDraw.Draw(line)
        draw.line((30,70,770,70),fill='black',width=2)
        draw.text((80,42),'Generated line '+str(index),fill=(90,90,90),font_size=16)
        line=line.rotate(-angle,Image.Resampling.BICUBIC,center=(400,70))
        image.paste(line,(0,row-70),line)
    return image


@pytest.mark.parametrize('direction',[1,-1])
@pytest.mark.parametrize('region',['top','middle','bottom'])
def test_drawn_rulings_at_any_height_become_level_and_stable_parts_stay_stable(direction,region):
    original=photographed_lines(direction,region)
    corrections=estimate_bands(original)
    assert len(corrections)>=3
    result=straighten_bands(original,corrections)
    padding=(result.height-original.height)//2
    assert result.width==original.width and padding>0
    stable_top,stable_bottom=(1000,1200) if region=='top' else (0,250)
    assert ImageChops.difference(original.crop((0,stable_top,800,stable_bottom)),result.crop((0,padding+stable_top,800,padding+stable_bottom))).getbbox() is None
    top,bottom={'top':(120,450),'middle':(430,760),'bottom':(850,1200)}[region]
    before=estimate_angle(original.crop((0,top,800,bottom)),horizontal_only=True)
    after=estimate_angle(result.crop((0,top+padding,800,bottom+padding)),horizontal_only=True)
    assert abs(before.angle)>1.5 and abs(after.angle)<.5


@pytest.mark.parametrize('mode',['RGB','L'])
def test_blank_and_already_level_pages_are_not_resampled(mode):
    image=Image.new(mode,(600,900),'white')
    assert estimate_bands(image)==()
    assert ImageChops.difference(image,straighten_photo(image)).getbbox() is None
    draw=ImageDraw.Draw(image)
    for y in range(50,880,55):
        draw.line((30,y,560,y),fill='black',width=2)
    assert estimate_bands(image)==()
    assert ImageChops.difference(image,straighten_photo(image)).getbbox() is None


def test_extra_white_space_keeps_colored_marks_at_all_four_edges():
    image=Image.new('RGB',(500,800),'white'); draw=ImageDraw.Draw(image)
    colors=((230,10,10),(10,180,10),(10,10,220),(200,10,200))
    for box,color in zip(((0,0,14,14),(485,0,499,14),(0,785,14,799),(485,785,499,799)),colors):
        draw.rectangle(box,fill=color)
    result=straighten_bands(image,((0,0),(.5,0),(.9,-3)))
    histogram=result.getcolors(result.width*result.height)
    for color in colors:
        assert sum(count for count,value in histogram if value==color)>100


@pytest.mark.parametrize('corrections',[
    ((.1,0),(.8,6)),((.5,0),(.2,2)),((.1,0),(.8,float('nan'))),
])
def test_invalid_controls_are_rejected(corrections):
    with pytest.raises(ValueError):
        straighten_bands(photographed_lines(),corrections)


def test_abrupt_shape_change_is_not_applied_and_cancel_stops():
    image=photographed_lines()
    result=straighten_bands(image,((.5,0),(.51,5)))
    assert result.size==image.size and ImageChops.difference(result,image).getbbox() is None
    with pytest.raises(OperationCancelled):
        straighten_photo(image,cancel=lambda:True)


@pytest.mark.parametrize('size,font_name,text',[
    ((800,1200),'arial.ttf','Office document: verify dates and reference numbers.'),
    ((1200,800),'times.ttf','Printed document: amounts and notes must remain readable.'),
    ((640,1000),'msyh.ttc','工程资料核对：文字、金额、日期应完整保留。'),
])
def test_text_without_rulings_across_fonts_sizes_and_page_orientations(size,font_name,text):
    font_path=Path('C:/Windows/Fonts')/font_name
    if not font_path.is_file():
        pytest.skip('This generated-font case needs the Windows system font.')
    width,height=size
    font_size=max(15,round(width*.025))
    font=ImageFont.truetype(str(font_path),font_size)
    while font.getlength(text)>width*.84:
        font_size-=1; font=ImageFont.truetype(str(font_path),font_size)
    image=Image.new('RGB',size,'white')
    for row in range(round(height*.08),round(height*.96),round(height*.07)):
        angle=.5+1.7*row/height
        strip=Image.new('RGBA',(width,160),(0,0,0,0)); draw=ImageDraw.Draw(strip)
        draw.text((round(width*.06),80),text,font=font,fill=(25,25,25),anchor='ls')
        strip=strip.rotate(-angle,Image.Resampling.BICUBIC,center=(width/2,80))
        image.paste(strip,(0,row-80),strip)
    draw=ImageDraw.Draw(image)
    draw.line((width*.94,height*.2,width*.91,height*.3,width*.98,height*.13),fill=(220,50,100),width=3)
    corrections=estimate_bands(image)
    assert corrections
    result=straighten_bands(image,corrections)
    padding=(result.height-height)//2
    original=image.crop((0,round(height*.55),width,round(height*.95)))
    corrected=result.crop((0,padding+round(height*.55),width,padding+round(height*.95)))
    assert abs(estimate_angle(original,horizontal_only=True).angle)>1.2
    assert abs(estimate_angle(corrected,horizontal_only=True).angle)<.7


@pytest.mark.parametrize('kind',['photographic_noise','tall_diagram','solid_dark'])
def test_non_document_counterexamples_are_left_unchanged(kind):
    import random
    randomizer=random.Random(812)
    image=Image.new('RGB',(600,900),'white')
    draw=ImageDraw.Draw(image)
    if kind=='photographic_noise':
        image.putdata([tuple(randomizer.randrange(256) for _ in range(3)) for _ in range(600*900)])
    elif kind=='tall_diagram':
        draw.rectangle((80,80,520,820),outline='black',width=4)
        draw.line((80,300,520,300,80,550,520,550),fill='black',width=3)
    else:
        draw.rectangle((0,0,600,900),fill=(20,20,20))
    assert estimate_bands(image)==()
    assert ImageChops.difference(image,straighten_photo(image)).getbbox() is None
