#!/usr/bin/env bash
PKG_NAME=mantid-total-scattering
USER=marshallmcdonnell

OS=${TRAVIS_OS_NAME}-64
mkdir ~/conda-bld
conda config --set anaconda_upload no
export CONDA_BLD_PATH=~/conda-bld
conda build .
export BUILD=$(ls ${CONDA_BLD_PATH}/${OS}/${PKG_NAME}* | sed -n "s/.*${PKG_NAME}-\(.*\)-\(.*\)\.tar.bz2/\1-\2/p")
echo "Uploading ${PKG_NAME}-${BUILD}.tar.bz2 artifact..."
anaconda -t ${CONDA_UPLOAD_TOKEN} upload -u ${USER} ${CONDA_BLD_PATH}/${OS}/${PKG_NAME}-${BUILD}.tar.bz2 --force

