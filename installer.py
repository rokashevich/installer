# encoding: utf-8

import os
import sys
import glob
import time
import shutil
import random
import threading
import subprocess
from enum import Enum, auto

#import PyQt5
#from PyQt5 import QtWidgets
#from PyQt5.QtWidgets import QLineEdit, QWidget, QTableView, QPushButton, QGridLayout, QFileDialog
#from PyQt5.QtGui import QFont, QIcon
#from PyQt5.QtCore import QAbstractTableModel, QVariant, Qt, pyqtSignal, QCoreApplication, QSettings, QSize

from PySide6 import QtWidgets, QtGui, QtCore

import helpers
from globals import Globals


class Host:
    class State(Enum):
        DISCOVERED = auto()
        IDLE = auto()
        QUEUED = auto()
        BASE_INSTALLING_SOURCE = auto()
        BASE_INSTALLING_DESTINATION = auto()
        BASE_SUCCESS = auto()
        CONF_NON_NEEDED = auto()
        CONF_INSTALLING = auto()
        CONF_SUCCESS = auto()
        CONF_FAILURE = auto()
        POST_NON_NEEDED = auto()
        POST_RUNNING = auto()
        POST_SUCCESS = auto()
        POST_FAILURE = auto()
        SUCCESS = auto()
        FAILURE = auto()


class TableData:
    class Host:
        def __init__(self, hostname, checked=True):
            self.hostname = hostname.lower()

            self.base_timer = None
            self.conf_counter_overwrite = None
            self.installation_timer = None
            self.conf_state = None
            self.post_state = None
            self.state = None
            self.checked = None

            self.reset()

            self.checked = checked

        def reset(self):
            self.base_timer = -1
            self.conf_counter_overwrite = 0
            self.installation_timer = 0
            self.conf_state = Host.State.IDLE
            self.post_state = Host.State.IDLE
            self.state = Host.State.IDLE
            self.checked = False

    def __init__(self, source, destination=''):
        self.source = source
        self.destination = destination if destination else self.source
        self.hosts = []

    def add_host(self, hostname, checked=True):
        self.hosts.append(TableData.Host(hostname, checked))
        self.hosts.sort(key=lambda x: x.hostname)


class TableModel(QtCore.QAbstractTableModel):
    def __init__(self, parent=None):
        QtCore.QAbstractTableModel.__init__(self, parent)
        self.dat = None
        self.clear()

    def changeData(self, new_data):
        self.data = new_data
        self.layoutChanged.emit()

    def updateRow(self, row):
        self.dataChanged.emit(QtCore.QAbstractTableModel.createIndex(self, 1, 0, self.table.model()),
                              QtCore.QAbstractTableModel.createIndex(self, 1, 1, self.table.model()))

    def updateTable(self):
        self.layoutChanged.emit()

    def rowCount(self, parent):
        if self.dat:
            return len(self.dat.hosts)
        else:
            return 0

    def columnCount(self, parent):
        return 2

    def data(self, index, role):
        if not index.isValid():
            return ''
        elif role != QtCore.Qt.DisplayRole:
            return ''
        elif index.column() == 0:  # checked
            return self.dat.hosts[index.row()]
        elif index.column() == 1:  # host
            return self.dat.hosts[index.row()]

    # Очистка модели.
    def clear(self):
        self.dat = TableData('', '')
        self.layoutChanged.emit()

    def add_hostname(self, hostname):
        self.dat.add_host(hostname)
        self.layoutChanged.emit()


class FirstColumnDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, parent):
        QtWidgets.QStyledItemDelegate.__init__(self, parent)

    def paint(self, painter, option, index):
        painter.save()
        font = painter.font()
        font.setPointSize(font.pointSize() * 1.5)
        painter.fillRect(option.rect, QtGui.QColor('#fff'))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor(
            '#000' if index.data().checked else '#b4b0aa')))
        painter.drawText(option.rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignCenter,
                         '●' if index.data().checked else '○')
        painter.restore()


class SecondColumnDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, parent):
        QtWidgets.QStyledItemDelegate.__init__(self, parent)

    def paint(self, painter, option, index):
        host = index.data()

        base_time = ''
        if host.base_timer >= 0:
            base_time = ' (%s)' % helpers.seconds_to_human(host.base_timer)

        if host.checked:
            pen_color = '#000000'
            if host.state == Host.State.BASE_INSTALLING_DESTINATION:
                text = 'Копирование base...%s' % base_time
                background_color = '#FFFFCC'
            elif host.state == Host.State.BASE_SUCCESS or host.state == Host.State.BASE_INSTALLING_SOURCE:
                text = 'Установлен base%s' % base_time
                background_color = '#FFFF66'
            elif host.state == Host.State.CONF_SUCCESS:
                text = 'Установлен base%s, conf' % (base_time)
                background_color = '#FFFF00'
            elif host.state == Host.State.POST_SUCCESS or host.state == Host.State.SUCCESS:
                text = 'Установлен base%s, conf; выполнен post-скрипт' % (
                    base_time)
                background_color = '#99FF66'
            elif host.state == Host.State.FAILURE:
                text = 'ОШИБКА'
                background_color = '#FF6633'
            elif host.state == Host.State.IDLE:
                text = 'Кликните, чтобы запустить только этот хост'
                background_color = '#ffffff'
            elif host.state == Host.State.QUEUED:
                text = 'Поставлен в очередь на установку (кликните, чтобы удалить из очереди)'
                background_color = '#ffffff'
            else:
                text = 'Этого режима быть не должно'
                background_color = '#ffffff'
        else:
            pen_color = '#b4b0aa'
            text = '-'
            background_color = '#ffffff'
        text = '  ' + host.hostname + '    ' + text
        painter.save()
        font = painter.font()
        font.setPointSize(font.pointSize() * 1.5)
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor(pen_color)))
        painter.fillRect(option.rect, QtGui.QColor(background_color))
        painter.drawText(option.rect, QtCore.Qt.AlignVCenter |
                         QtCore.Qt.AlignLeft, text)
        painter.restore()


class Installer(QtWidgets.QWidget):
    def closeEvent(self, event):
        self.do_stop_begin()
        event.accept()

    class State(Enum):
        QUEUED = auto()  # переходное состояние
        DEFAULT = auto()  # по умолчанию: всё disabled, кроме button_browse
        PREPARING = auto()  # скачивание/распаковка дистрибутива: всё disabled, кроме browse>stop
        # дистрибутив распакован: stop>browse, конфигурации, остальное заблокировано
        PREPARED = auto()
        CONF_SELECTED = auto()  # выбрана конфигурация: всё разблокировано
        INSTALLING = auto()  # установка: start>stop, остальное заблокировано

    class Distribution:

        def __init__(self, uri):
            self.uri = uri  # zip-дистрибутив или base*.txt
            self.base_txt = ''  # Полный путь к base*.txt
            self.configurations_dir = ''  # Полный путь к распакованному директории conf
            self.name = ''  # Имя дистрибутива
            self.base = ''
            self.size = 0
            self.prepare_timer = 0
            self.installation_timer = 0  # <=0 - процесс не запущен, >0 - процесс идёт
            self.executables = []

    configuration_changed = QtCore.Signal()
    state_changed = QtCore.Signal()
    row_changed = QtCore.Signal(int)
    table_changed = QtCore.Signal()
    worker_needed = QtCore.Signal()
    window_title_changed = QtCore.Signal()

    def clear(self):
        self.post_install_scripts_dict = {}
        self.distribution = None
        self.do_verify = True
        self.stop = False
        self.pids = set()
        self.configurations = []
        self.table_data_dict = {}
        self.prepare_message = ''
        self.prepare_process_download = None
        self.copy_conf_in_progress = False
        self.configurations_list.setModel(
            QtCore.QStringListModel(self.configurations))
        self.installation_path.setText('')

    def __init__(self):
        super().__init__()

        self.version = open('version.txt').read().rstrip(
        ) if os.path.exists('version.txt') else 'DEV'
        self.hostname = subprocess.check_output(
            'hostname').decode(errors='ignore').strip().lower()

        self.post_install_scripts_dict = None
        self.distribution = None
        self.do_verify = None
        self.stop = None
        self.pids = None
        self.configurations = None
        self.table_data_dict = None
        self.prepare_message = None
        self.prepare_process_download = None
        self.copy_conf_in_progress = None

        self.configurations_list = QtWidgets.QListView()
        self.installation_path = QtWidgets.QLineEdit()
        self.table = QtWidgets.QTableView()
        self.table.setModel(TableModel())
        self.table.setItemDelegateForColumn(0, FirstColumnDelegate(self))
        self.table.setItemDelegateForColumn(1, SecondColumnDelegate(self))
        self.table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        # Отключение выделения ячеек
        self.table.setFocusPolicy(QtCore.Qt.NoFocus)
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)  # при нажатии
        self.table.verticalHeader().setVisible(False)  # Отключение нумерации
        self.table.horizontalHeader().setVisible(False)  # ячеек

        self.clear()

        self.button_browse = QtWidgets.QPushButton()
        self.button_start = QtWidgets.QPushButton('Старт')
        self.button_base = QtWidgets.QPushButton("base")
        self.button_conf = QtWidgets.QPushButton("conf")
        # С пробелам по краям чтобы в зачёркнутом состоянии было виднее.
        self.button_do_verify = QtWidgets.QPushButton(" md5 ")

        gl = QtWidgets.QGridLayout(self)

        # fromRow, fromColumn, rowSpan, columnSpan
        # If rowSpan and/or columnSpan is -1, then the widget will extend to the bottom and/or right edge, respectively.
        # https://doc.qt.io/qt-5/qgridlayout.html#addWidget-2

        gl.addWidget(self.button_browse, 0, 0, 1, 1)  #
        gl.addWidget(self.button_start, 0, 1, 1, 1)  # Верхний ряд кнопок
        gl.addWidget(self.button_base, 0, 2, 1, 1)  #
        gl.addWidget(self.button_conf, 0, 3, 1, 1)  #
        gl.addWidget(self.button_do_verify, 0, 4, 1, 1)  #

        gl.addWidget(self.installation_path, 1, 0, 1, 5)  #
        gl.addWidget(self.configurations_list, 2, 0,
                     1, 5)  # Элементы друг над другом

        gl.addWidget(self.table, 0, 6, -1, 1)  # Контейнер: консоль или лог

        self.setLayout(gl)

        self.window_title_changed.emit()

        self.show()

        self.button_browse.clicked.connect(self.on_clicked_button_browse)
        self.button_start.clicked.connect(self.on_clicked_button_start)
        self.button_base.clicked.connect(self.on_clicked_button_base)
        self.button_conf.clicked.connect(self.on_clicked_button_conf)
        self.button_do_verify.clicked.connect(self.on_clicked_button_do_verify)
        self.table.clicked.connect(self.on_clicked_table)
        self.state_changed.connect(self.on_state_changed)
        self.table_changed.connect(self.on_table_changed)
        self.worker_needed.connect(self.worker)
        self.window_title_changed.connect(self.on_title_changed)
        self.installation_path.textChanged.connect(
            self.on_installation_path_changed)

        self.state = Installer.State.DEFAULT
        self.state_changed.emit()
        self.window_title_changed.emit()

        def installation_timer():
            while True:
                if self.state == Installer.State.INSTALLING:
                    self.distribution.installation_timer += 1
                    self.window_title_changed.emit()
                time.sleep(1)
                if not threading.main_thread().is_alive():
                    sys.exit()

        threading.Thread(target=installation_timer).start()

    def on_table_changed(self):
        self.table.model().updateTable()

    def on_state_changed(self):
        if self.state == Installer.State.DEFAULT:
            self.configurations_list.setEnabled(False)
            self.installation_path.setEnabled(False)
            self.button_start.setEnabled(False)
            self.button_browse.setText('Открыть')
            self.button_browse.setEnabled(True)
            self.button_base.setEnabled(False)
            self.button_conf.setEnabled(False)
            self.button_do_verify.setEnabled(False)
            self.table.setEnabled(False)

        elif self.state == Installer.State.PREPARING:
            self.configurations_list.setEnabled(False)
            self.installation_path.setEnabled(False)
            self.button_start.setEnabled(False)
            self.button_browse.setText('Отменить')
            self.button_browse.setEnabled(True)
            self.button_base.setEnabled(False)
            self.button_conf.setEnabled(False)
            self.button_do_verify.setEnabled(False)
            self.table.setEnabled(False)

        # Распакован архив
        elif self.state == Installer.State.PREPARED:
            self.button_browse.setText('Открыть')
            self.button_browse.setEnabled(True)
            self.button_start.setText('Старт')
            self.button_base.setEnabled(True)
            self.button_conf.setEnabled(True)
            self.button_do_verify.setEnabled(True)
            self.configurations_list.setEnabled(True)
            self.installation_path.setEnabled(True)

            if self.configurations_list.model().rowCount() == 0:
                self.configurations_list.setModel(
                    QtCore.QStringListModel(self.configurations))
                self.configurations_list.selectionModel(
                ).currentChanged.connect(self.on_conf_selected)
                self.configurations_list.setMinimumWidth(
                    self.configurations_list.sizeHintForColumn(0)
                    + 2 * self.configurations_list.frameWidth()
                )
            self.button_start.setEnabled(True)

            self.table.setEnabled(True)

        elif self.state == Installer.State.INSTALLING:
            self.button_browse.setEnabled(False)
            self.button_base.setEnabled(False)
            self.button_conf.setEnabled(False)
            self.button_do_verify.setEnabled(False)
            self.configurations_list.setEnabled(False)
            self.installation_path.setEnabled(False)
            self.button_start.setText('Стоп')
            self.table.setEnabled(True)

        self.window_title_changed.emit()

    def on_clicked_table(self, index):
        if self.stop:  # Если находимся в режиме останова то игнорируем клики в таблице
            return

        column = index.column()
        host = self.table.model().dat.hosts[index.row()]
        if column == 0:
            host.checked = not host.checked
        elif column == 1:
            if host.state == Host.State.IDLE or host.state == Host.State.SUCCESS or host.state == Host.State.FAILURE:
                helpers.Logger.i('Запуск %s' % host.hostname)
                host.state = Host.State.QUEUED
                self.worker_needed.emit()
            elif host.state == Host.State.QUEUED:
                host.state = Host.State.IDLE
            else:
                return
        self.table_changed.emit()

    def on_conf_selected(self):  # Выбрали мышкой конфигурацию
        self.button_conf.setEnabled(True)

        # Название выбранной конфигурации, т.е. название папки в каталоге conf в распакованном дистрибутиве.
        conf_name = self.configurations[self.configurations_list.currentIndex(
        ).row()]

        # Выставляем установочный путь из settings.txt
        self.installation_path.setEnabled(True)
        self.installation_path.setText(
            self.table_data_dict[conf_name].destination)

        # Выставляем новые данные в правой панели
        self.fill_table(conf_name)

    def fill_table(self, key):
        model = self.table.model()
        model.clear()
        for host in self.table_data_dict[key].hosts:
            model.add_hostname(host.hostname)

    def on_installation_path_changed(self):
        if self.installation_path.text() != '':
            self.button_start.setEnabled(True)
        else:
            self.button_start.setEnabled(False)

    def on_clicked_button_browse(self):
        if not self.state == Installer.State.PREPARING:
            settings = QtCore.QSettings()
            default_browse_path = settings.value(
                'default_browse_path', r'C:\\', type=str)
            options = QtWidgets.QFileDialog.Options()
            options |= QtWidgets.QFileDialog.DontUseNativeDialog
            file, _ = QtWidgets.QFileDialog.getOpenFileName(self,
                                                            'Выберите дистрибутив или укажите '
                                                            'base.txt в распакованном дистрибутиве', default_browse_path,
                                                            'Дистрибутив (*.zip base*.txt)', options=options)
            if not file:  # При выборе дистрибутива нажали Cancel.
                self.state = Installer.State.DEFAULT
                return
            else:  # Выбрали файл дистрибутива.
                self.clear()
                file = os.path.abspath(file)
                settings.setValue('default_browse_path', os.path.dirname(file))
                settings.sync()

            threading.Thread(target=self.prepare_distribution,
                             args=(file,)).start()
        else:
            threading.Thread(target=self.prepare_distribution_stop).start()
            self.reset()

    def on_clicked_button_start(self):
        if not self.state == Installer.State.INSTALLING:
            self.do_start_spider()
        else:
            self.do_stop_begin()

    def do_stop_begin(self):
        self.stop = True
        self.button_browse.setEnabled(False)
        self.button_start.setEnabled(False)
        self.configurations_list.setEnabled(False)
        self.installation_path.setEnabled(False)

        threading.Thread(target=self.do_stop_end).start()

    def do_stop_end(self):
        cmd = r'taskkill /t /f'
        for pid in self.pids:
            cmd += r' /pid ' + str(pid)
        self.pids.clear()
        if cmd != r'taskkill /t /f':
            subprocess.run(cmd, shell=True)

        for host in self.table.model().dat.hosts:
            host.state = Host.State.IDLE
        self.table_changed.emit()

        self.state = Installer.State.PREPARED
        self.state_changed.emit()

        self.stop = False
        self.button_browse.setEnabled(True)
        self.button_start.setEnabled(True)
        self.configurations_list.setEnabled(True)
        self.installation_path.setEnabled(True)

    def do_start_spider(self):
        for host in [host for host in self.table.model().dat.hosts if host.checked]:
            if (host.state == Host.State.IDLE
                    or host.state == Host.State.FAILURE
                    or host.state == Host.State.SUCCESS):
                host.state = Host.State.QUEUED
        self.worker_needed.emit()

    def on_clicked_button_base(self):
        helpers.open_folder(self.distribution.base)

    def on_clicked_button_conf(self):
        helpers.open_folder(os.path.join(self.distribution.configurations_dir,
                                         self.configurations[self.configurations_list.currentIndex().row()]))

    def on_clicked_button_do_verify(self):
        if self.do_verify:
            self.button_do_verify.setStyleSheet(
                "text-decoration: line-through;")
            self.do_verify = False
        else:
            self.button_do_verify.setStyleSheet("")
            self.do_verify = True

    @staticmethod
    def on_clicked_button_about(self):
        page = os.path.join(os.path.dirname(
            os.path.realpath(__file__)), 'about', 'about.html')
        os.system('start ' + page)

    def remove_pid(self, pid):
        try:
            self.pids.remove(pid)
        except:
            pass

    def do_copy_base(self, source_host, destination_host):
        def timer():
            while destination_host.state == Host.State.BASE_INSTALLING_DESTINATION:
                if not threading.main_thread().is_alive():
                    return
                self.table_changed.emit()
                time.sleep(1)
                destination_host.base_timer += 1

        destination_host.base_timer = 0
        threading.Thread(target=timer).start()

        # Останов процессов, запущенных из места установки.
        if sys.platform == 'win32':
            if self.hostname != destination_host.hostname:
                auth = ' /node:"%s" /user:"%s" /password:"%s"' \
                       % (destination_host.hostname, Globals.samba_login, Globals.samba_password)
            else:
                auth = ''
            cmd = r'wmic%s process list full' % auth
            helpers.Logger.i(cmd)
            r = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes = []
            for line in list(filter(None, [line.strip() for line in r.stdout.decode(errors='ignore').splitlines()])):
                if line.startswith('ExecutablePath='):
                    processes.append([line.split('=')[1], None])
                if line.startswith('Handle=') and not processes[-1][1]:
                    processes[-1][1] = line.split('=')[1]
            for process in processes:
                path = process[0].lower()
                if path.startswith(self.installation_path.text().lower()):
                    pid = process[1]
                    if self.hostname != destination_host.hostname:
                        auth = ' /s %s /u %s /p %s' \
                               % (destination_host.hostname, Globals.samba_login, Globals.samba_password)
                    else:
                        auth = ''
                    cmd = 'taskkill%s /t /f /pid %s' % (auth, pid)
                    helpers.Logger.i(cmd)
                    subprocess.run(cmd, shell=True)
        else:
            pass  # TODO Сделать останов процессов из места установки для Linux!

        # Удаление существующего каталога установки, если необходимо.
        if sys.platform == 'win32':
            if self.hostname != destination_host.hostname:
                auth = r'PsExec64.exe -accepteula -nobanner \\%s -u %s -p %s -c -f ' \
                       % (destination_host.hostname, Globals.samba_login, Globals.samba_password)
            else:
                auth = ''
            cmd = r'%smake-empty.exe "%s"' % (auth,
                                              self.installation_path.text())
            helpers.Logger.i(cmd)
            r = subprocess.Popen(cmd, shell=True)
            self.pids.add(r.pid)
            r.wait()
            if self.stop:
                return
            self.remove_pid(r.pid)
            if r.returncode != 0:
                helpers.Logger.e(
                    'На %s не удалось удалить %s' % (destination_host.hostname, self.installation_path.text()))
                if source_host:
                    source_host.state = Host.State.BASE_SUCCESS
                destination_host.state = Host.State.FAILURE
                self.worker_needed.emit()
                return
        else:
            cmd = 'ssh root@%s "rm -rf \"%s\" ; mkdir -p \"%s\""' \
                  % (destination_host.hostname, self.installation_path.text(), self.installation_path.text())
            subprocess.run(cmd, shell=True)

        # Шаг 2: Копирование base.
        source_hostname = source_host.hostname if source_host else None
        source_path = self.installation_path.text(
        ) if source_host else self.distribution.base

        if source_hostname:  # Копирование с удалённого хоста на удалённый.
            r = helpers.sync_remote_to_remote(source_hostname, source_path, destination_host.hostname, source_path,
                                              Globals.samba_login, Globals.samba_password)
        else:  # Копирование с локального хоста на удалённый.
            r = helpers.copy_from_local_to_remote(source_path, destination_host.hostname,
                                                  self.installation_path.text().strip(), True)

        self.pids.add(r.pid)
        r.wait()
        self.remove_pid(r.pid)

        if r.returncode != 0:
            if source_host:
                source_host.state = Host.State.BASE_SUCCESS
            destination_host.state = Host.State.FAILURE
            self.worker_needed.emit()
            return

        # Шаг 3: проверка md5 по base.txt.
        result = Host.State.BASE_SUCCESS
        if self.do_verify:
            if sys.platform == 'win32':
                if self.hostname != destination_host.hostname:
                    cmd = (r'PsExec64.exe -accepteula -nobanner \\%s -u %s -p %s -w %s -c -f verify-md5.exe %s'
                           % (destination_host.hostname, Globals.samba_login, Globals.samba_password,
                              self.installation_path.text(), os.path.basename(self.distribution.base_txt)))
                else:
                    cmd = (r'cd /d %s & %s'
                           % (self.installation_path.text(),
                              os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                           'verify-md5.exe %s' % os.path.basename(self.distribution.base_txt))))
                helpers.Logger.i(cmd)
                r = subprocess.run(
                    cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            else:
                cmd = 'scp %s root@%s:%s' \
                      % (os.path.join(os.path.dirname(os.path.realpath(__file__)), 'verify-md5'),
                         destination_host.hostname, self.installation_path.text())
                helpers.Logger.i(cmd)
                subprocess.run(cmd, shell=True)
                cmd = 'ssh root@%s "cd \"%s\";chmod +x verify-md5;./verify-md5 %s"' % (
                    destination_host.hostname, self.installation_path.text(), os.path.basename(self.distribution.base_txt))
                helpers.Logger.i(cmd)
                r = subprocess.run(
                    cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                os.system('ssh root@%s rm "%s/verify-md5"' %
                          (destination_host.hostname, self.installation_path.text()))
            returncode = r.returncode
            stdout = r.stdout.decode(errors='ignore')
            files_with_mismatched_md5 = list(
                filter(None, [file.strip() for file in (stdout.split('\n'))]))
            if returncode:
                result = Host.State.FAILURE
                if files_with_mismatched_md5:
                    for file in files_with_mismatched_md5:
                        helpers.Logger.e('%s: ошибка md5: %s' %
                                         (destination_host.hostname, file))
        destination_host.state = result
        if source_host:
            source_host.state = Host.State.BASE_SUCCESS
        self.worker_needed.emit()

    def do_copy_conf(self):
        hosts = []  # Заполним хостами, на которые надо будет установить conf
        for host in [host for host in self.table.model().dat.hosts if host.checked]:
            if host.state == Host.State.BASE_SUCCESS:
                hosts.append(host)
        conf_name = self.configurations[self.configurations_list.currentIndex(
        ).row()]
        common_path = os.path.join(
            self.distribution.configurations_dir, conf_name, 'common')
        for host in hosts:
            self.table_changed.emit()
            hostname = host.hostname

            # Подкаталог конфигураций common не обязателен!

            if os.path.isdir(common_path):
                r = helpers.copy_from_local_to_remote(common_path, hostname,
                                                      self.installation_path.text().strip(), False)
                r.wait()
                if r.returncode != 0:
                    host.state = Host.State.FAILURE
                    continue

            personal_path = os.path.join(
                self.distribution.configurations_dir, conf_name, hostname)
            r = helpers.copy_from_local_to_remote(personal_path, hostname,
                                                  self.installation_path.text().strip(), False)
            r.wait()
            if r.returncode != 0:
                host.state = Host.State.FAILURE
                continue
            host.state = host.conf_state = Host.State.CONF_SUCCESS
        self.worker_needed.emit()

    def do_run_post_script(self):
        etc = os.path.join(self.installation_path.text(), 'etc')
        s = os.path.join(etc, 'post-install')
        if sys.platform == 'win32':
            s += '.bat'
        else:
            s += '.sh'
        for host in [host for host in self.table.model().dat.hosts if host.checked]:
            if host.state == Host.State.CONF_SUCCESS:
                if sys.platform == 'win32':
                    cmd = r'PsExec64.exe \\' + host.hostname + ' -u ' + Globals.samba_login + ' -p ' \
                          + Globals.samba_password + ' ' + s
                else:
                    cmd = 'ssh root@%s "chmod +x \"%s/*.sh\"; \"%s\""' % (
                        host.hostname, etc, s)
                r = subprocess.run(cmd, shell=True)
                if r.returncode:
                    host.state = host.post_state = Host.State.FAILURE
                    helpers.Logger.i(
                        'Ошибка выполнения post-скрипта: command=%s returncode=%d' % (cmd, r.returncode))
                else:
                    host.state = host.post_state = Host.State.POST_SUCCESS
                self.table_changed.emit()
        self.worker_needed.emit()

    def worker(self):
        if not self.state == Installer.State.INSTALLING:
            self.distribution.installation_timer = 0
            self.state = Installer.State.INSTALLING
            self.state_changed.emit()

        # Копирование base
        have_source_host = False
        any_base_copy_started = False
        for source_host in [host for host in self.table.model().dat.hosts if host.checked]:
            if source_host.state == Host.State.BASE_SUCCESS:
                have_source_host = True
                possible_destination_hosts = []
                for destination_host in [host for host in self.table.model().dat.hosts if host.checked]:
                    if destination_host.state == Host.State.QUEUED:
                        possible_destination_hosts.append(destination_host)
                if possible_destination_hosts:
                    destination_host = random.choice(
                        possible_destination_hosts)
                    source_host.state = Host.State.BASE_INSTALLING_SOURCE
                    destination_host.state = Host.State.BASE_INSTALLING_DESTINATION
                    helpers.Logger.i('Копирование base: %s -> %s' % (source_host.hostname,
                                                                     destination_host.hostname))
                    threading.Thread(target=self.do_copy_base, args=(
                        source_host, destination_host)).start()
                    any_base_copy_started = True
        if not have_source_host:  # Нет source-хоста но возможно уже запущенно какое-то копирование
            for host in [host for host in self.table.model().dat.hosts if host.checked]:
                if host.state == Host.State.BASE_INSTALLING_DESTINATION:
                    have_source_host = True
                    break
        if not have_source_host:
            first_host = None
            for destination_host in [host for host in self.table.model().dat.hosts if host.checked]:
                if destination_host.hostname == self.hostname and destination_host.state == destination_host.state.QUEUED:
                    first_host = destination_host  # Начинаем с локального компьютера, если возможно
                    break
            if not first_host:
                for destination_host in [host for host in self.table.model().dat.hosts if host.checked]:
                    if destination_host.state == destination_host.state.QUEUED:
                        first_host = destination_host
                        break
            if first_host:
                first_host.state = Host.State.BASE_INSTALLING_DESTINATION
                helpers.Logger.i(
                    'Копирование base: localhost -> %s' % first_host.hostname)
                first_host.base_timer = -1
                threading.Thread(target=self.do_copy_base,
                                 args=(None, first_host)).start()
                any_base_copy_started = True
        if any_base_copy_started:
            return

        # Если хотя бы один QUEUED, то значит ещё не везде ещё скопирован base - выходим.
        for host in [host for host in self.table.model().dat.hosts if host.checked]:
            if (host.state == Host.State.QUEUED or host.state == Host.State.BASE_INSTALLING_SOURCE
                    or host.state == Host.State.BASE_INSTALLING_DESTINATION):
                return

        # Если нет ни одного QUEUED, значит все так или иначе прошли копирование base - поэтому ищем BASE_SUCCESS
        # и ставим копирование conf.
        for host in [host for host in self.table.model().dat.hosts if host.checked]:
            if host.state == Host.State.BASE_SUCCESS:
                threading.Thread(target=self.do_copy_conf).start()
                return

        # Выполнение post-скриптов
        s = os.path.join(self.distribution.configurations_dir,
                         self.configurations[self.configurations_list.currentIndex(
                         ).row()],
                         'common', 'etc', 'post-install')
        if sys.platform == 'win32':
            s += '.bat'
        else:
            s += '.sh'
        is_prepare_script_used = False
        if os.path.exists(s):
            is_prepare_script_used = True
            for host in [host for host in self.table.model().dat.hosts if host.checked]:
                if host.state == Host.State.CONF_SUCCESS:
                    threading.Thread(target=self.do_run_post_script).start()
                    return
        success_state = Host.State.BASE_SUCCESS
        if is_prepare_script_used:
            success_state = Host.State.POST_SUCCESS
        else:
            success_state = Host.State.CONF_SUCCESS

        for host in [host for host in self.table.model().dat.hosts if host.checked]:
            if host.state != Host.State.FAILURE and host.state != Host.State.SUCCESS and host.state != Host.State.IDLE:
                if host.state == success_state:
                    host.state = Host.State.SUCCESS
                    self.table_changed.emit()
                else:
                    return

        self.state = Installer.State.PREPARED
        self.state_changed.emit()

    def prepare_distribution(self, uri):
        def timer():
            while self.state == Installer.State.PREPARING:
                if not threading.main_thread().is_alive():
                    sys.exit()
                self.state_changed.emit()
                time.sleep(1)
                self.distribution.prepare_timer += 1
                self.window_title_changed.emit()

        self.state = Installer.State.PREPARING

        threading.Thread(target=timer).start()

        helpers.Logger.reset()
        helpers.Logger.i('Открываем %s' % uri)

        self.distribution = Installer.Distribution(uri)

        self.configurations.clear()
        self.table_data_dict.clear()
        self.post_install_scripts_dict.clear()
        self.state_changed.emit()

        if os.path.basename(uri).startswith('base') and os.path.basename(uri).endswith('.txt'):
            base_txt = uri
        else:
            unpack_to = self.unpack_distribution(uri)
            g = glob.glob(os.path.join(unpack_to, 'base', 'base*.txt'))
            if len(g) != 1:  # файл вида base*.txt в корне распакованного дистрибутива должен быть только один!
                self.state = Installer.State.DEFAULT
                helpers.Logger.e('После распаковки не найден base*.txt')
                self.state_changed.emit()
                return
            base_txt = g[0]

        conf = os.path.join(os.path.dirname(base_txt), '..', 'conf')
        if os.path.isdir(conf):
            for name in os.listdir(conf):
                destination = ''
                settings_txt = os.path.join(conf, name, 'settings.txt')
                if os.path.isfile(settings_txt):
                    destination = open(
                        settings_txt).readline().strip().split()[1]
                table_data = TableData(os.path.dirname(base_txt), destination)
                for hostname in os.listdir(os.path.join(conf, name)):
                    if (hostname == 'common' or
                            not os.path.isdir(os.path.join(conf, name, hostname))):
                        continue
                    table_data.add_host(hostname)
                self.configurations.append(name)
                self.table_data_dict[name] = table_data

        self.configurations.sort()

        configurations_dir = os.path.abspath(
            os.path.join(os.path.dirname(base_txt), '..', 'conf'))
        if os.path.isdir(configurations_dir):
            self.distribution.configurations_dir = configurations_dir
        for line in open(base_txt, errors='ignore').readlines():
            if line.startswith('name '):
                self.distribution.name = line.split(' ')[1].strip()
                continue
        if not self.distribution.name:
            self.distribution.name = os.path.basename(self.distribution.uri)
        self.distribution.base_txt = base_txt
        self.distribution.base = os.path.dirname(self.distribution.base_txt)

        # Сканируем дистрибутив и создаём список исполняемых файлов для отстрела перед установкой
        for root, dirs, files in os.walk(self.distribution.base):
            for file in files:
                if file.endswith('.exe'):
                    self.distribution.executables.append(file)

        def get_path_size():
            for dirpath, dirnames, filenames in os.walk(self.distribution.base):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        self.distribution.size += os.path.getsize(fp)
                    except OSError as e:
                        helpers.Logger.w(e)
                    self.window_title_changed.emit()
            self.distribution.size = -self.distribution.size
            self.window_title_changed.emit()

        threading.Thread(target=get_path_size).start()

        # Очищаем все добавленные хосты от какой-либо информации, оставшейся с прошлого раза (если есть)
        for host in self.table.model().dat.hosts:
            host.reset()

        self.state = Installer.State.PREPARED
        self.state_changed.emit()

        return

    def prepare_distribution_stop(self):
        pass

    @staticmethod
    def unpack_distribution(file):
        unpack_to = os.path.splitext(file)[0]  # отрезаем .zip
        if os.path.exists(unpack_to):
            helpers.Logger.i('Удаление каталога распаковки')
            shutil.rmtree(unpack_to)
        helpers.Logger.i('Создание каталога распаковки %s' % unpack_to)
        os.makedirs(unpack_to)
        cmd = '7za x "' + file + '" -aoa -o"' + unpack_to + '"'
        helpers.Logger.i(cmd)
        r = subprocess.run(cmd, shell=True)
        if r.returncode != 0:
            helpers.Logger.w('Сбой при распаковке архива, архив битый?')
        return unpack_to

    def on_title_changed(self):
        title = QtCore.QCoreApplication.applicationName() + ' ' + self.version

        if not self.distribution:  # Самый первый запуск, никакой дистрибутив ещё не открыт.
            self.setWindowTitle(title)
            return

        if not self.distribution.name:  # Имя дистрибутива ещё не доступно - занчит происходит его открытие
            title += ' • Распаковка: ' + self.distribution.uri + '... ' \
                     + helpers.seconds_to_human(self.distribution.prepare_timer)
            self.setWindowTitle(title)
            return

        title += ' • Дистрибутив: ' + self.distribution.name

        if self.distribution.uri.endswith('.zip') or self.distribution.uri.endswith('.tar.xz'):
            title += ' распакован за %s' % helpers.seconds_to_human(
                self.distribution.prepare_timer)

        title += ' ' + helpers.bytes_to_human(abs(self.distribution.size))
        if self.distribution.size > 0:
            title += '...'

        if self.state == Installer.State.INSTALLING:
            title += ' • Установка... ' + \
                helpers.seconds_to_human(self.distribution.installation_timer)
        elif self.state == Installer.State.PREPARED:
            if self.distribution.installation_timer > 0:
                title += ' • Завершено ' + \
                    helpers.seconds_to_human(
                        self.distribution.installation_timer)

        self.setWindowTitle(title)
