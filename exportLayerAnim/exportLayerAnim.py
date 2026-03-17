from krita import *
import os
import json
from . import exportLayerAnimDialog

# Couleurs RGB des labels AE (issues du JSX)
AE_LABEL_COLORS = [
    (0,   0,   0),    # 0  None
    (121, 58,  58),   # 1  Red
    (144, 138, 68),   # 2  Yellow
    (115, 132, 130),  # 3  Aqua
    (145, 124, 131),  # 4  Pink
    (115, 115, 131),  # 5  Lavender
    (146, 127, 109),  # 6  Peach
    (120, 130, 120),  # 7  Sea Foam
    (82,  93,  142),  # 8  Blue
    (67,  112, 68),   # 9  Green
    (101, 52,  107),  # 10 Purple
    (146, 103, 37),   # 11 Orange
    (94,  65,  51),   # 12 Brown
    (152, 85,  137),  # 13 Fuchsia
    (61,  111, 113),  # 14 Cyan
    (114, 105, 90),   # 15 Sandstone
    (45,  62,  45),   # 16 DarkGreen
]

KRITA_TO_AE_LABEL = {
    0: 0, 1: 3, 2: 9, 3: 14, 4: 2,
    5: 1, 6: 10, 7: 11, 8: 0, 9: 0,
    10: 15, 11: 12,
}


class ExportLayerAnim(Extension):

    def __init__(self, parent):
        super(ExportLayerAnim, self).__init__(parent)

        # Png save info
        self.pngInfo = InfoObject()
        self.pngInfo.setProperty("alpha", True)
        self.pngInfo.setProperty("compression", 3)
        self.pngInfo.setProperty("forceSRGB", False)
        self.pngInfo.setProperty("indexed", False)
        self.pngInfo.setProperty("interlaced", False)
        self.pngInfo.setProperty("saveSRGBProfile", False)
        self.pngInfo.setProperty("transparencyFillcolor", [0,0,0])

        # Jpg save info
        self.jpgInfo = InfoObject()
        self.jpgInfo.setProperty('quality', 85)           # 0 (high compression/low quality) to 100 (low compression/higher quality)
        self.jpgInfo.setProperty('smoothing', 0)          # 0 to 100
        self.jpgInfo.setProperty('subsampling', 0)        # 0=4:2:0 (smallest file size)   1=4:2:2    2=4:4:0     3=4:4:4 (Best quality)
        self.jpgInfo.setProperty('progressive', False)
        self.jpgInfo.setProperty('optimize', True)
        self.jpgInfo.setProperty('saveProfile', True)
        self.jpgInfo.setProperty("transparencyFillcolor", [0,0,0])

    # Krita.instance() exists, so do any setup work
    def setup(self):
        pass

    def mkdir(self, directory):
        if (os.path.exists(directory) and os.path.isdir(directory)):
            return

        try:
            os.makedirs(directory)
        except OSError as e:
            raise e

    def isLayerAnimated(self, node):
        if self.firstFrame:
            return False
        if node.animated():
            return True
        elif node.type() == "grouplayer":
            child = node.childNodes()
            for c in child:
                if c.animated():
                    return True
                elif c.type() == "grouplayer" and self.isLayerAnimated(c):
                    return True
        return False

    def hasKeyframeAtTime(self, node, i):
        if node.hasKeyframeAtTime(i):
            return True
        elif node.type() == "grouplayer":
            child = node.childNodes()
            for c in child:
                if c.hasKeyframeAtTime(i):
                    return True
                elif c.type() == "grouplayer" and self.isLayerAnimated(c):
                    return True
        return False

    def getCompositionIdx(self, name):
        # Get composition docker, view, model
        docker = next(d for d in Application.dockers() if d.objectName() == 'CompositionDocker')
        view = docker.findChild(QListView, 'compositionView')
        model = view.model()

        # Get a composition based on its name
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            text = index.data(role=Qt.DisplayRole)
            if text == name:
                return index
        else:
            raise RuntimeError(f'Unknown composition. (did get: {name=})')

    # A generator activating compositions one by one then returning to the previous state
    def composition(self):
        if not self.useCompositions:
            yield ""
            return

        # Get composition docker
        docker = next(d for d in Application.dockers() if d.objectName() == 'CompositionDocker')
        view = docker.findChild(QListView, 'compositionView')
        model = view.model()

        meta = docker.metaObject()
        activated = meta.method(meta.indexOfSlot(b'activated(QModelIndex)'))

        # Save current state
        saveClicked = meta.method(meta.indexOfSlot(b'saveClicked()'))
        saveClicked.invoke(docker)

        try:
            # Browse all composition
            for row in range(model.rowCount() - 1): # -1 because of the saved compo
                index = model.index(row, 0)
                checked_state = index.data(role=Qt.CheckStateRole)
                if checked_state == Qt.Checked:
                    # Activate the composition
                    activated.invoke(docker, Q_ARG("QModelIndex", index))
                    yield index.data(role=Qt.DisplayRole)

        finally:
            lastIdx = model.index(model.rowCount() - 1, 0)

            # Reverse to the previous state
            activated.invoke(docker, Q_ARG("QModelIndex", lastIdx))

            # Remove the tmp save
            selectionModel = view.selectionModel()
            selectionModel.setCurrentIndex(lastIdx, QItemSelectionModel.Current)
            deleteClicked = meta.method(meta.indexOfSlot(b'deleteClicked()'))
            deleteClicked.invoke(docker)

    def initForExport(self):
        self.doc = Application.activeDocument()
        if not self.doc: return

        self.layersName = ""

        p = os.path.split(self.doc.fileName())
        self.exportPath = p[0]
        filename = os.path.splitext(p[1])[0]
        path = self.exportPath + "/" + filename
        if (os.path.exists(path) and os.path.isdir(path)):
            self.exportDir = filename
            self.namePrefix = ""
        else:
            self.exportDir = ""
            self.namePrefix = filename
        self.extension = "png"
        self.useCompositions = False
        self.firstFrame = False

    def isNodeEffectivelyVisible(self, node):
        if not node.visible():
            return False
        parent = node.parentNode()
        while parent:
            if not parent.visible():
                return False
            parent = parent.parentNode()
        return True


    def export(self):
        Application.setBatchmode(True)

        num_frames = self.doc.fullClipRangeEndTime() + 1 # self.doc.animationLength()
        num_digits = len(str(num_frames))
        fps = self.doc.framesPerSecond()

        # Create the folder if missing
        self.exportPath = self.exportPath + "/" + self.exportDir
        self.mkdir(self.exportPath)

        # Structure JSON conforme au format attendu par le script JSX TVPaint/AE
        json_output = {
            "version": {
                "major": 6,
                "minor": 0
            },
            "project": {
                "clip": {
                    "name": self.doc.name(),
                    "width": self.doc.width(),
                    "height": self.doc.height(),
                    "pixelaspectratio": 1.0,
                    "framerate": float(fps),
                    "image-count": num_frames,
                    "bg": {
                        "red": 255,
                        "green": 255,
                        "blue": 255
                    },
                    "layers": []
                }
            }
        }

        clip_layers = json_output["project"]["clip"]["layers"]

        haveAnimatedLayers = False
        for compo in self.composition():
            topLevelLayers = self.doc.topLevelNodes()
            animatedLayers = []

            for node in topLevelLayers:
                if (node.type() not in ["paintlayer", "clonelayer", "grouplayer", "filelayer", "vectorlayer"]):
                    continue
                if not self.isNodeEffectivelyVisible(node):
                    continue
                if "NE" in node.name() or node.name() == "No Name":
                    continue

                if node.type() == "grouplayer" and "EC" in node.name():
                    child = node.childNodes()
                    for c in child:
                        topLevelLayers.append(c)
                else:
                    if self.isLayerAnimated(node):
                        # Trouver start/end réels du calque animé
                        layer_start = self._findLayerStart(node, num_frames)
                        #layer_end   = self._findLayerEnd(node, num_frames)
                        clip_start = self.doc.fullClipRangeStartTime()
                        clip_end = self.doc.fullClipRangeEndTime()

                        # Récupérer la couleur du calque Krita
                        color_index = node.colorLabel()
                        with open(self.exportPath + "/debug.log", "a") as log:
                            log.write(f"color_index() pour '{node.name()}': {color_index} (type: {type(color_index)})\\n")
                        ae_label = KRITA_TO_AE_LABEL.get(color_index, 0)
                        r, g, b = AE_LABEL_COLORS[ae_label]

                        # Couleur de groupe (label AE) — noir par défaut
                        new_layer_data = {
                            "name": node.name(),
                            "visible": "true",
                            "opacity": float(node.opacity()),  # 0–255
                            "start": clip_start,
                            "end": clip_end,
                            "blending-mode": "Color",   # mode normal par défaut
                            "pre-behavior": 0,
                            "post-behavior": 0,
                            "group": {
                                "red":   r,
                                "green": g,
                                "blue":  b
                            },
                            "link": []
                        }
                        clip_layers.append(new_layer_data)

                        animatedLayers.append({
                            'node': node,
                            'frame_count': 0,
                            'last_instance_index': -1,
                            'link_list': new_layer_data["link"],
                            'layer_start': clip_start,
                            'layer_end': clip_end
                        })
                    else:
                        self.exportLayer(node, compo)

            # Export des calques animés
            if len(animatedLayers) > 0:
                haveAnimatedLayers = True

                for i in range(num_frames):
                    self.doc.setCurrentTime(i)

                    for layer in animatedLayers:
                        node = layer['node']
                        node_name = node.name()
                        link_list = layer['link_list']

                        # Nouvelle keyframe → on exporte l'image et on crée une entrée link
                        if self.hasKeyframeAtTime(node, i):
                            suffix = "_" + str(layer['frame_count']).zfill(num_digits)
                            subdir = node_name
                            self.exportLayer(node, compo, suffix, subdir=subdir)

                            # Reconstruire le nom réel tel que exportLayer l'a généré
                            prefix = compo
                            sep = "_" if (self.namePrefix != "" or prefix != "") and node_name != "" else ""
                            real_filename = f'{self.namePrefix}{prefix}{sep}{node_name}{suffix}.{self.extension}'
                            file_relative = subdir + "/" + real_filename

                            instance_name = node_name + suffix

                            new_link_entry = {
                                "instance-name": instance_name,
                                "file": file_relative,
                                "images": []       # liste des frames qui affichent cette image
                            }
                            link_list.append(new_link_entry)

                            layer['frame_count'] += 1
                            layer['last_instance_index'] = len(link_list) - 1

                        # Associer cette frame à la dernière image exportée
                        if layer['last_instance_index'] >= 0:
                            link_list[layer['last_instance_index']]["images"].append(i)

        # Inverser l'ordre des calques avant écriture
        json_output["project"]["clip"]["layers"] = list(reversed(clip_layers))

        # Écriture du fichier JSON
        json_file_path = os.path.join(self.exportPath, "import_ae.json")
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(json_output, f, indent=4)

        # Undo du setCurrentTime
        if haveAnimatedLayers and num_frames > 0:
            Application.action('edit_undo').trigger()
            self.doc.save()

        Application.setBatchmode(False)


    def _findLayerStart(self, node, num_frames):
        """Retourne le premier frame où le calque a une keyframe."""
        for i in range(num_frames):
            if self.hasKeyframeAtTime(node, i):
                return i
        return 0

    #def _findLayerEnd(self, node, num_frames):
    #    """Retourne le dernier frame où le calque a une keyframe."""
    #    last = 0
    #    for i in range(num_frames):
    #        if self.hasKeyframeAtTime(node, i):
    #            last = i
    #    return last


    # Create a file for the specified layer
    def exportLayer(self, node, prefix="", suffix="", subdir=""):
        self.doc.waitForDone()
        fileName = f'{self.namePrefix}{prefix}{"_" if (self.namePrefix != "" or prefix != "") and node.name() != "" else ""}{node.name()}{suffix}.{self.extension}'
        self.layersName += "\n" + fileName

        # Si un sous-dossier est demandé, on l'utilise
        if subdir:
            targetPath = self.exportPath + "/" + subdir
            self.mkdir(targetPath)  # création si manquant
        else:
            targetPath = self.exportPath

        path = targetPath + "/" + fileName
        bounds = QRect(0, 0, self.doc.width(), self.doc.height())
        if self.extension == "png":
            node.save(path, self.doc.resolution() / 72., self.doc.resolution() / 72., self.pngInfo, bounds)
        else:
            node.save(path, self.doc.resolution() / 72., self.doc.resolution() / 72., self.jpgInfo, bounds)

    # Show a dialogue asking for the folder name and files prefix
    def exportDialog(self):
        self.initForExport()
        if not self.doc: return

        self.mainDialog = exportLayerAnimDialog.ExportLayerAnimDialog(self, Application.activeWindow().qwindow())
        self.mainDialog.initialize()

    # called after setup(self)
    def createActions(self, window):
        action = window.createAction("export_layer_anim", i18n("Export layer anim"))
        action.setToolTip(i18n("Plugin to manipulate properties of selected documents."))
        action.triggered.connect(self.exportDialog)