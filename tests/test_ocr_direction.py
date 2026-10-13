"""Page consensus avoids rotating symmetric numeric fields independently."""
from types import SimpleNamespace
from app.ocr.worker import stabilize_short_directions


class Crop:
    def __init__(self,width,rotation=0):
        self.shape=(20,width,3); self.rotation=rotation
    def copy(self):
        return Crop(self.shape[1],self.rotation)
    def __getitem__(self,key):
        return Crop(self.shape[1],180-self.rotation)


def result(labels):
    return SimpleNamespace(cls_res=[(label,.99) for label in labels],img_list=[Crop(100 if index<3 else 40,180 if label=='180' else 0) for index,label in enumerate(labels)])


def test_upright_page_keeps_short_numeric_fields_upright():
    crops=[Crop(100),Crop(100),Crop(100),Crop(40)]
    classified=result(['0','0','0','180'])
    stabilize_short_directions(crops,classified,.9)
    assert classified.img_list[-1].rotation==0


def test_upside_down_page_corrects_short_fields_with_page():
    crops=[Crop(100),Crop(100),Crop(100),Crop(40)]
    classified=result(['180','180','180','0'])
    stabilize_short_directions(crops,classified,.9)
    assert classified.img_list[-1].rotation==180


def test_ambiguous_page_and_long_independent_region_keep_classifier_choice():
    crops=[Crop(100),Crop(100),Crop(100),Crop(40),Crop(100)]
    classified=SimpleNamespace(cls_res=[('0',.99),('0',.99),('180',.99),('180',.99),('180',.99)],img_list=list(crops))
    before=classified.img_list.copy()
    stabilize_short_directions(crops,classified,.9)
    assert all(a is b for a,b in zip(before,classified.img_list))
